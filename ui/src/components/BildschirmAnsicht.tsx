"use client";

import { memo, useCallback, useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent, type PointerEvent } from "react";
import { useJetzt } from "@/components/kommando/useJetzt";
import { codeBestaetigen } from "@/lib/geraeteVerwaltungApi";
import { GRUND_TEXT, liveBildLoeschen, restZeit, zeichnerSetzen } from "@/lib/liveBild";
import { accessToken } from "@/lib/supabase";
import { useSelect } from "@/lib/store";
import type { LiveBildAktionen } from "@/lib/useWamo";
import { istOnline, wamo } from "@/lib/wamo";

const TITEL_ID = "lb-titel";
const VERSTECKT_MAX_MS = 30_000; // so lange darf die Ansicht unsichtbar sein (Tab im Hintergrund, Fenster im Tray), dann endet sie

/** Fokus auf die Überschrift, wenn sich die Karte ändert (der gedrückte Knopf verschwindet sonst). */
const fokusAufTitel = () => requestAnimationFrame(() => document.getElementById(TITEL_ID)?.focus());

const LUPE_KANTE = 168; // Pixel der Lupe auf dem Bildschirm
const LUPE_FAKTOR = 2.5;

/** Darf diese Sitzung das Live-Bild überhaupt anbieten? Nur Admin mit laufender PC-Steuerung am gebundenen Laptop. */
export const useLiveBildErlaubt = () =>
  useSelect(wamo, (s) => istOnline(s) && s.profil?.rolle === "admin" && s.betrieb?.pcSteuerung === "an" && (s.betrieb.pc === "verbunden" || s.betrieb.pc === "gesperrt"));

/**
 * Ansicht des eigenen Laptops (Live-Bild). Die Bilder kommen einzeln vom Kern, nur wenn die Person am Laptop den Schalter
 * eingeschaltet hat; sie leben hier nur auf der Zeichenfläche (lib/liveBild.ts), werden nie gespeichert und verschwinden
 * beim Schließen, bei Pause, Sperre und Ende. Funktionen: Monitorwahl, Lupe, Restzeit bis zum automatischen Ende.
 */
export const BildschirmAnsicht = memo(function BildschirmAnsicht({ aktionen }: { aktionen: LiveBildAktionen }) {
  const erlaubt = useLiveBildErlaubt();
  const stand = useSelect(wamo, (s) => s.liveBild);
  const offen = stand.geoeffnet;
  const zustand = stand.zustand;

  // Verbindung weg oder PC-Steuerung aus: Ansicht zu (der Kern beendet sie ohnehin)
  useEffect(() => {
    if (offen && !erlaubt) {
      liveBildLoeschen();
      wamo.set((s) => ({ ...s, liveBild: { ...s.liveBild, geoeffnet: false, zustand: "aus" } }));
    }
  }, [offen, erlaubt]);

  if (!erlaubt && !offen) return null;
  return (
    <section className="lb" aria-labelledby={TITEL_ID} data-zustand={zustand}>
      <h2 id={TITEL_ID} className="lb-titel" tabIndex={-1}>
        Live-Bild des Laptops
      </h2>
      {offen ? <Offen aktionen={aktionen} /> : <Geschlossen aktionen={aktionen} />}
    </section>
  );
});

/**
 * Fernanfrage (Entscheidung Niko 04.11.2026): Am Handy gibt es keinen Tray. Hier bittet der Admin seinen eigenen Laptop um das
 * Live-Bild, mit einem frischen Code der Authenticator-App. Der Kern prüft Token und Code, die Desktop-App am Laptop schaltet nur
 * ein, wenn dort die Einwilligung schon vorliegt, und zeigt den roten Rahmen. Das Token geht nur in diese eine Nachricht.
 */
function Anfordern({ aktionen, monitor }: { aktionen: LiveBildAktionen; monitor: number }) {
  const [offen, setOffen] = useState(false);
  const [code, setCode] = useState("");
  const [fehler, setFehler] = useState("");
  const [beschaeftigt, setBeschaeftigt] = useState(false);
  const laeuft = useRef(false);
  const feld = useRef<HTMLInputElement>(null);
  const id = useId();

  const abschicken = async (e: FormEvent) => {
    e.preventDefault();
    if (laeuft.current) return;
    const sauber = code.replace(/\s+/g, "");
    if (!/^\d{6}$/.test(sauber)) {
      setFehler("Bitte die sechs Ziffern aus der Authenticator-App eingeben.");
      feld.current?.focus();
      return;
    }
    laeuft.current = true;
    setBeschaeftigt(true);
    setFehler("");
    try {
      const bestaetigt = await codeBestaetigen(sauber);
      if (!bestaetigt.ok) {
        setFehler(bestaetigt.text);
        setCode("");
        feld.current?.focus();
        return;
      }
      const token = await accessToken();
      if (!token) {
        setFehler("Die Anmeldung ist nicht mehr gültig. Bitte lade die Seite neu.");
        return;
      }
      if (aktionen.anfordern(monitor, token)) {
        setOffen(false);
        setCode("");
        fokusAufTitel();
      } else {
        setFehler("Die Anfrage konnte nicht gesendet werden.");
      }
    } finally {
      laeuft.current = false;
      setBeschaeftigt(false);
    }
  };

  if (!offen) {
    return (
      <button
        type="button"
        className="btn-secondary btn-small"
        onClick={() => {
          setOffen(true);
          requestAnimationFrame(() => feld.current?.focus());
        }}
      >
        Vom Laptop einschalten lassen
      </button>
    );
  }
  return (
    <form className="lb-code" onSubmit={abschicken} noValidate>
      <label htmlFor={`${id}-code`}>Code der Authenticator-App</label>
      <div className="lb-code-zeile">
        <input
          ref={feld}
          id={`${id}-code`}
          className="lb-code-feld"
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={7}
          value={code}
          onChange={(e) => setCode(e.target.value)}
          aria-describedby={fehler ? `${id}-fehler` : undefined}
          disabled={beschaeftigt}
        />
        <button type="submit" className="btn-secondary btn-small" disabled={beschaeftigt}>
          {beschaeftigt ? "Prüfe …" : "Anfordern"}
        </button>
        <button
          type="button"
          className="btn-secondary btn-small"
          disabled={beschaeftigt}
          onClick={() => {
            setOffen(false);
            setCode("");
            setFehler("");
          }}
        >
          Abbrechen
        </button>
      </div>
      {fehler && (
        <p id={`${id}-fehler`} className="lb-hinweis" role="alert">
          {fehler}
        </p>
      )}
      <p className="lb-klein">
        Am Laptop leuchtet dann der rote Rahmen, und es erscheint eine Meldung. Das geht nur, wenn dort das Live-Bild schon einmal
        eingewilligt wurde. Es läuft höchstens 30 Minuten.
      </p>
    </form>
  );
}

function Geschlossen({ aktionen }: { aktionen: LiveBildAktionen }) {
  const { text, grund, monitor } = useSelect(wamo, (s) => s.liveBild);
  const erklaerung = text || (grund ? GRUND_TEXT[grund] : undefined);
  return (
    <>
      <p className="lb-text">
        Du siehst den Bildschirm deines eigenen Laptops, solange das Live-Bild dort eingeschaltet ist: im Tray-Menü von WAMO am Laptop
        oder von hier aus mit einem Code der Authenticator-App. Am Laptop leuchtet dann ein roter Rahmen. Es wird nichts aufgezeichnet
        oder gespeichert.
      </p>
      {erklaerung && (
        <p className="lb-hinweis" role="status">
          {erklaerung}
        </p>
      )}
      <div className="lb-aktionen">
        <button type="button" className="btn-secondary btn-small" onClick={() => {
            aktionen.an(monitor);
            fokusAufTitel();
          }}>
          Live-Bild öffnen
        </button>
        <Anfordern aktionen={aktionen} monitor={monitor} />
      </div>
    </>
  );
}

const ZUSTAND_TEXT = {
  wartet: "Warte darauf, dass das Live-Bild am Laptop eingeschaltet wird.",
  laeuft: "Das Bild läuft. Am Laptop ist der rote Rahmen zu sehen.",
  pausiert: "Das Bild ruht.",
  aus: "",
} as const;

function Offen({ aktionen }: { aktionen: LiveBildAktionen }) {
  const stand = useSelect(wamo, (s) => s.liveBild);
  const flaeche = useRef<HTMLCanvasElement>(null);
  const lupeFlaeche = useRef<HTMLCanvasElement>(null);
  const [lupe, setLupe] = useState(false);
  const [lupePos, setLupePos] = useState<{ x: number; y: number } | null>(null);
  const takt = useJetzt(1000);

  const lupeZeichnen = useCallback((pos: { x: number; y: number } | null) => {
    const quelle = flaeche.current;
    const ziel = lupeFlaeche.current;
    if (!quelle || !ziel || !pos || quelle.width === 0) return;
    const ctx = ziel.getContext("2d");
    if (!ctx) return;
    const sw = LUPE_KANTE / LUPE_FAKTOR;
    const sx = Math.min(Math.max(pos.x * quelle.width - sw / 2, 0), Math.max(0, quelle.width - sw));
    const sy = Math.min(Math.max(pos.y * quelle.height - sw / 2, 0), Math.max(0, quelle.height - sw));
    ctx.clearRect(0, 0, LUPE_KANTE, LUPE_KANTE);
    ctx.drawImage(quelle, sx, sy, sw, sw, 0, 0, LUPE_KANTE, LUPE_KANTE);
  }, []);

  const pos = useRef(lupePos);
  useEffect(() => {
    pos.current = lupePos;
  }, [lupePos]);

  // Zeichnen und Löschen anmelden: nur hier landet ein Bild, nur im Arbeitsspeicher
  useEffect(() => {
    const knoten = flaeche.current;
    const abmelden = zeichnerSetzen(
      (bild) => {
        const c = flaeche.current;
        const ctx = c?.getContext("2d");
        if (c && ctx) {
          if (c.width !== bild.width || c.height !== bild.height) {
            c.width = bild.width;
            c.height = bild.height;
          }
          ctx.drawImage(bild, 0, 0);
          lupeZeichnen(pos.current);
        }
      },
      () => {
        // Hauptfläche UND Lupe leeren: nach Pause, Sperre oder Ende bleibt kein Ausschnitt stehen
        const c = flaeche.current;
        c?.getContext("2d")?.clearRect(0, 0, c.width, c.height);
        const l = lupeFlaeche.current;
        l?.getContext("2d")?.clearRect(0, 0, l.width, l.height);
        setLupePos(null);
      },
    );
    return () => {
      abmelden();
      knoten?.getContext("2d")?.clearRect(0, 0, knoten.width, knoten.height);
    };
  }, [lupeZeichnen]);

  // Ansicht unsichtbar (Tab im Hintergrund, Fenster im Tray): nach 30 s beenden, damit niemand unbemerkt weiter überträgt
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const pruefen = () => {
      if (timer) clearTimeout(timer);
      timer = null;
      if (document.visibilityState === "hidden") timer = setTimeout(() => aktionen.aus(), VERSTECKT_MAX_MS);
    };
    document.addEventListener("visibilitychange", pruefen);
    pruefen();
    return () => {
      document.removeEventListener("visibilitychange", pruefen);
      if (timer) clearTimeout(timer);
    };
  }, [aktionen]);

  // Pause, Warten, Sperre: kein Bild stehen lassen
  useEffect(() => {
    if (stand.zustand !== "laeuft") liveBildLoeschen();
  }, [stand.zustand]);

  const rest = stand.endeMs ? Math.max(0, Math.round((stand.endeMs - takt.datum) / 1000)) : null;
  const text = stand.text || (stand.grund ? GRUND_TEXT[stand.grund] : "") || ZUSTAND_TEXT[stand.zustand];

  const monitorTaste = (e: KeyboardEvent<HTMLDivElement>) => {
    if (stand.monitore < 2) return;
    const d = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const n = ((stand.monitor - 1 + d + stand.monitore) % stand.monitore) + 1;
    aktionen.monitor(n);
    requestAnimationFrame(() => document.querySelector<HTMLElement>(".lb-monitore [aria-checked='true']")?.focus());
  };
  const lupeBewegt = (e: PointerEvent<HTMLDivElement>) => {
    if (!lupe) return;
    const r = e.currentTarget.getBoundingClientRect();
    const p = { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
    setLupePos(p);
    lupeZeichnen(p);
  };
  const lupeTaste = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!lupe) return;
    const schritt = 0.05;
    const d = { ArrowLeft: [-schritt, 0], ArrowRight: [schritt, 0], ArrowUp: [0, -schritt], ArrowDown: [0, schritt] }[e.key];
    if (!d) return;
    e.preventDefault();
    const alt = pos.current ?? { x: 0.5, y: 0.5 };
    const p = { x: Math.min(1, Math.max(0, alt.x + d[0])), y: Math.min(1, Math.max(0, alt.y + d[1])) };
    setLupePos(p);
    lupeZeichnen(p);
  };

  return (
    <>
      <div className="lb-kopf">
        <p className="lb-zustand" role="status">
          <span className="lb-punkt" aria-hidden="true" data-zustand={stand.zustand} />
          {text}
        </p>
        {rest !== null && stand.zustand === "laeuft" && (
          <p className="lb-rest num">
            Endet automatisch in <time dateTime={`PT${rest}S`}>{restZeit(rest)}</time>
          </p>
        )}
      </div>

      <div className="lb-werkzeuge">
        <div role="radiogroup" aria-label="Monitor" className="betriebsart-wahl lb-monitore" onKeyDown={monitorTaste}>
          {Array.from({ length: stand.monitore }, (_, i) => i + 1).map((n) => (
            <button
              key={n}
              type="button"
              role="radio"
              tabIndex={stand.monitor === n || (stand.monitor > stand.monitore && n === 1) ? 0 : -1}
              aria-checked={stand.monitor === n}
              data-an={stand.monitor === n ? "" : undefined}
              disabled={stand.monitore < 2}
              onClick={() => aktionen.monitor(n)}
            >
              Monitor {n}
            </button>
          ))}
        </div>
        <button type="button" className="kz-chip" aria-pressed={lupe} onClick={() => setLupe((l) => !l)}>
          Lupe
        </button>
        <button
          type="button"
          className="btn-secondary btn-small lb-zu"
          onClick={() => {
            aktionen.aus();
            fokusAufTitel();
          }}
        >
          Live-Bild beenden
        </button>
      </div>
      {stand.zustand === "wartet" && <Anfordern aktionen={aktionen} monitor={stand.monitor} />}
      {stand.monitore < 2 && <p className="lb-klein">Dieser Laptop hat nur einen Monitor, eine Auswahl gibt es nicht.</p>}

      <div
        className="lb-rahmen"
        data-leer={stand.zustand !== "laeuft" ? "" : undefined}
        data-lupe={lupe ? "" : undefined}
        tabIndex={lupe ? 0 : -1}
        role={lupe ? "group" : undefined}
        aria-label={lupe ? "Bild mit Lupe, Pfeiltasten bewegen die Lupe" : undefined}
        onContextMenu={(e) => e.preventDefault()} // kein "Bild speichern": das Bild wird nie gespeichert
        onPointerMove={lupeBewegt}
        onPointerLeave={() => setLupePos(null)}
        onKeyDown={lupeTaste}
      >
        <canvas ref={flaeche} className="lb-bild" role="img" aria-label="Bildschirm des Laptops" width={16} height={9} />
        {stand.zustand !== "laeuft" && <p className="lb-platzhalter">{text}</p>}
        <canvas
          ref={lupeFlaeche}
          className="lb-lupe"
          width={LUPE_KANTE}
          height={LUPE_KANTE}
          aria-hidden="true"
          hidden={!lupe || !lupePos || stand.zustand !== "laeuft"}
          style={lupePos ? { left: `calc(${lupePos.x * 100}% - ${LUPE_KANTE / 2}px)`, top: `calc(${lupePos.y * 100}% - ${LUPE_KANTE + 12}px)` } : undefined}
        />
      </div>
      <p className="lb-klein">Nichts wird aufgezeichnet. Mit dem Schließen, einer Sperre oder dem Ende des Gesprächs ist das Bild weg.</p>
    </>
  );
}
