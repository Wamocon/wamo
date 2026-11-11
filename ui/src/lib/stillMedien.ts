"use client";

import { WavMediaManager } from "@pipecat-ai/small-webrtc-transport";

/**
 * Medienmanager für den stillen Start beim Tippen (KP-1A): Er öffnet das Mikrofon erst, wenn die Person "Sprechen" verlangt.
 *
 * Warum nicht einfach WavMediaManager mit enableMic: false: Der Standardmanager ruft beim Verbinden immer WavRecorder.begin() auf
 * (initialize), also getUserMedia, und der Transport legt die dabei entstandene Spur danach auf den Sender (tracks().local.audio). Ein
 * "stiller" Start mit dem Standardmanager würde die Berechtigungsfrage zeigen, den Aufnahmepunkt im Tab einschalten und Audio senden
 * (stillMedien.comp.test.tsx hält das fest). Dieser Manager öffnet bis dahin nichts: keine Spur am Sender, also keine Audiopakete.
 *
 * Sprechen: enableMic(true) holt das Mikrofon nach (begin, dann die Aufnahme des Standardmanagers). Der Rückruf onTrackStarted meldet die
 * neue Spur wie beim normalen Start, die Oberfläche legt sie auf den Sender (lib/mikrofon.ts, sendeSpurErsetzen). Scheitert das Öffnen
 * (gesperrt, kein Gerät), meldet die Bibliothek onDeviceError, und die Sitzung bleibt still.
 *
 * Die Basisklasse hat nur lose Typen (die Typen ihres Pakets sind nicht eingebunden), deshalb greifen wir auf ihre Felder über einen Typ zu.
 */

type Basis = {
  _initialized: boolean;
  _micEnabled: boolean;
  _callbacks: { onDeviceError?: (e: unknown) => void };
  _wavRecorder: {
    begin: (id?: string) => Promise<unknown>;
    end: () => Promise<unknown>;
    listenForDeviceChange: (f: ((d: unknown[]) => void) | null) => void;
    listenForDeviceErrors: (f: ((e: unknown) => void) | null) => void;
  };
  _handleAvailableDevicesUpdated: (d: unknown[]) => void;
  _handleDeviceError: (e: unknown) => void;
};

type Spuren = { local: { audio?: MediaStreamTrack } };

export class StillMedienManager extends WavMediaManager {
  // Geerbte Mitglieder, die der Transport und die Tests brauchen (die Typen der Basisklasse sind nicht eingebunden); declare erzeugt nichts
  declare setClientOptions: (optionen: unknown) => void;
  declare readonly isMicEnabled: boolean;

  /** Das Mikrofon ist geöffnet (nach "Sprechen"). */
  #offen = false;
  /**
   * Die Sitzung ist beendet (disconnect). Wartet enableMic gerade auf die Berechtigung (begin), kommt die Antwort erst danach: Ohne
   * diese Marke nähme die Seite ohne Sitzung auf (Aufnahmepunkt im Tab bis zum Neuladen). Initialize setzt sie für eine neue Sitzung zurück.
   */
  #abgebaut = false;
  /** Gerätewahl der Person, gilt erst beim Öffnen. */
  #wunschId: string | undefined;

  get #b(): Basis {
    return this as unknown as Basis;
  }

  /** Kein WavRecorder.begin: nichts wird geöffnet, nichts abgefragt. */
  async initialize(): Promise<void> {
    this.#abgebaut = false;
    this.#b._initialized = true;
  }

  async connect(): Promise<void> {
    if (this.#offen) await super.connect();
  }

  async disconnect(): Promise<void> {
    this.#abgebaut = true;
    if (!this.#offen) {
      this.#b._initialized = false;
      return;
    }
    this.#offen = false;
    try {
      await super.disconnect();
    } catch {
      // schon beendet: nichts mehr zu tun
    }
  }

  /** Ohne geöffnetes Mikrofon keine lokale Spur: Der Sender bleibt leer. */
  tracks(): Spuren {
    return this.#offen ? (super.tracks() as Spuren) : { local: {} };
  }

  async getAllMics(): Promise<unknown[]> {
    return this.#offen ? super.getAllMics() : [];
  }

  /** Vor dem Öffnen wird die Wahl nur gemerkt (listDevices und begin würden sonst nach der Berechtigung fragen). */
  async updateMic(micId: string): Promise<void> {
    if (!this.#offen) {
      this.#wunschId = micId || undefined;
      return;
    }
    await super.updateMic(micId);
  }

  async enableMic(enable: boolean): Promise<void> {
    if (this.#offen) {
      await super.enableMic(enable);
      return;
    }
    this.#b._micEnabled = false; // ohne Öffnen bleibt es bei "aus"
    if (!enable || this.#abgebaut) return; // nach dem Ende der Sitzung öffnet nichts mehr
    try {
      const rec = this.#b._wavRecorder;
      rec.listenForDeviceErrors(null);
      rec.listenForDeviceErrors(this.#b._handleDeviceError.bind(this)); // vor begin: eine Ablehnung soll onDeviceError melden
      await rec.begin(this.#wunschId); // erst hier getUserMedia
      if (this.#abgebaut) {
        // Die Sitzung endete, während die Person die Berechtigung erteilte: das eben geöffnete Mikrofon sofort wieder schließen
        await rec.end().catch(() => undefined);
        return;
      }
      this.#offen = true;
      rec.listenForDeviceChange(null);
      rec.listenForDeviceChange(this.#b._handleAvailableDevicesUpdated.bind(this)); // wie im Standardmanager erst nach begin
      await super.enableMic(true); // Spur einschalten und aufnehmen, meldet onTrackStarted
      if (this.#abgebaut) {
        // Die Sitzung endete während des Einschaltens: nichts offen lassen
        this.#offen = false;
        this.#b._micEnabled = false;
        await super.disconnect().catch(() => undefined);
      }
    } catch {
      // Die Bibliothek hat den Fehler schon über onDeviceError gemeldet (begin). Die Sitzung bleibt still.
      const warOffen = this.#offen;
      this.#offen = false;
      this.#b._micEnabled = false;
      // Ein halb geöffnetes Mikrofon nie offen lassen (begin war gelungen, die Aufnahme nicht)
      if (warOffen) await super.disconnect().catch(() => undefined);
    }
  }
}
