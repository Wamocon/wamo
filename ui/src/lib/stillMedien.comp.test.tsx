// Beleg für den stillen Start (lib/stillMedien.ts, KP-1A): Ohne Klick auf "Sprechen" öffnet die Oberfläche das Mikrofon nie.
//
// Befund aus dem Code der Transportbibliothek (@pipecat-ai/small-webrtc-transport 1.11): Der WavMediaManager öffnet das Mikrofon beim
// Verbinden IMMER (initialize() ruft WavRecorder.begin() und damit getUserMedia), auch mit enableMic: false. Danach legt der Transport
// diese Spur auf den Sender (tracks().local.audio): Mit dem Standard-Manager würde ein "stiller" Start also Audio senden. Der erste Test
// hält diesen Befund fest (er ist der Grund für StillMedienManager), die übrigen belegen, dass der stille Manager kein Mikrofon öffnet
// und keine Spur auf den Sender legt, bis "Sprechen" es verlangt.
// Ausführen im Ordner ui/:  npm run test:komponenten
import { WavMediaManager } from "@pipecat-ai/small-webrtc-transport";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StillMedienManager } from "@/lib/stillMedien";

// Die Bibliothek erzeugt beim Laden Blob-Adressen für ihre Audio-Worklets (URL.createObjectURL), das kennt jsdom nicht
vi.hoisted(() => {
  (URL as unknown as { createObjectURL: (b: unknown) => string }).createObjectURL = () => "blob:wamo-test";
});

type Optionen = { enableMic: boolean; enableCam: boolean; callbacks: Record<string, ((...a: unknown[]) => void) | undefined> };

function optionen(extra: Partial<Optionen["callbacks"]> = {}): Optionen {
  return { enableMic: false, enableCam: false, callbacks: { ...extra } };
}

describe("stiller Medienmanager (kein Mikrofon ohne Klick)", () => {
  let getUserMedia: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    // Zugriff verweigert: so lässt sich zählen, ohne dass echte Geräte oder Audio-APIs nötig sind
    getUserMedia = vi.fn().mockRejectedValue(Object.assign(new Error("verweigert"), { name: "NotAllowedError" }));
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia,
        enumerateDevices: vi.fn().mockResolvedValue([]),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      },
    });
    Object.defineProperty(navigator, "permissions", {
      configurable: true,
      value: { query: vi.fn().mockResolvedValue({ state: "granted" }) },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("Befund: der Standardmanager öffnet das Mikrofon beim Verbinden auch mit enableMic false", async () => {
    const m = new WavMediaManager();
    m.setClientOptions(optionen());
    // Das Verbinden scheitert danach an fehlenden Audio-APIs der Testumgebung, der Beleg ist der Aufruf davor
    await m.connect().catch(() => undefined);
    expect(getUserMedia).toHaveBeenCalledTimes(1);
  });

  it("initialize, connect und disconnect öffnen kein Mikrofon", async () => {
    const m = new StillMedienManager();
    m.setClientOptions(optionen());
    await m.initialize();
    await m.connect();
    await m.disconnect();
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("es gibt keine lokale Spur für den Sender: ohne Spur gehen keine Audiopakete hinaus", async () => {
    const m = new StillMedienManager();
    m.setClientOptions(optionen());
    await m.connect();
    // Der Transport legt in addUserMedia nur eine vorhandene Spur auf den Sender (replaceTrack); ohne Spur bleibt der Sender leer
    expect(m.tracks()).toEqual({ local: {} });
    expect(m.isMicEnabled).toBe(false);
  });

  it("Geräteliste und Gerätewechsel fragen kein Mikrofon an", async () => {
    const m = new StillMedienManager();
    m.setClientOptions(optionen());
    await m.connect();
    expect(await m.getAllMics()).toEqual([]);
    await m.updateMic("gerät-1");
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("Mikrofon aus ausschalten bleibt folgenlos und öffnet nichts", async () => {
    const m = new StillMedienManager();
    m.setClientOptions(optionen());
    await m.connect();
    await m.enableMic(false);
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(m.isMicEnabled).toBe(false);
  });

  it("erst enableMic(true) (Sprechen) öffnet das Mikrofon, genau einmal; eine Ablehnung meldet onDeviceError und wirft nicht", async () => {
    const onDeviceError = vi.fn();
    const m = new StillMedienManager();
    m.setClientOptions(optionen({ onDeviceError }));
    await m.connect();
    expect(getUserMedia).not.toHaveBeenCalled();
    await expect(m.enableMic(true)).resolves.toBeUndefined();
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(onDeviceError).toHaveBeenCalledTimes(1);
    // das Öffnen ist gescheitert: kein Mikrofon, keine Spur, Zustand wieder still
    expect(m.isMicEnabled).toBe(false);
    expect(m.tracks()).toEqual({ local: {} });
  });

  it("Trennen, während die Berechtigung noch aussteht: das Mikrofon wird sofort geschlossen und nie eingeschaltet", async () => {
    const m = new StillMedienManager();
    m.setClientOptions(optionen());
    await m.initialize();
    await m.connect();
    const rec = (m as unknown as { _wavRecorder: { begin: unknown; end: unknown } })._wavRecorder;
    let erlauben: () => void = () => undefined;
    rec.begin = vi.fn(() => new Promise<void>((fertig) => (erlauben = fertig))); // die Person hat noch nicht auf die Frage geantwortet
    rec.end = vi.fn().mockResolvedValue(undefined);
    const basisEnable = vi.spyOn(WavMediaManager.prototype, "enableMic");
    const sprechen = m.enableMic(true);
    await m.disconnect(); // die Sitzung endet, bevor die Antwort kommt
    erlauben(); // jetzt erteilt die Person die Berechtigung
    await sprechen;
    expect(rec.end).toHaveBeenCalledTimes(1); // sofort wieder geschlossen (kein Aufnahmepunkt ohne Sitzung)
    expect(basisEnable).not.toHaveBeenCalled(); // nie eingeschaltet
    expect(m.isMicEnabled).toBe(false);
    expect(m.tracks()).toEqual({ local: {} });
    getUserMedia.mockClear();
    await m.enableMic(true); // eine beendete Sitzung öffnet nichts mehr
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(rec.begin).toHaveBeenCalledTimes(1);
  });

  it("nach gescheitertem Öffnen lässt sich Sprechen noch einmal versuchen", async () => {
    const m = new StillMedienManager();
    m.setClientOptions(optionen());
    await m.connect();
    await m.enableMic(true);
    await m.enableMic(true);
    expect(getUserMedia).toHaveBeenCalledTimes(2);
    await m.disconnect(); // wirft nicht, obwohl nie ein Mikrofon lief
  });
});
