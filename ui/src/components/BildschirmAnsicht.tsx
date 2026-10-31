"use client";

import { memo, useCallback, useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { useJetzt } from "@/components/kommando/useJetzt";
import { GRUND_TEXT, liveBildLoeschen, restZeit, zeichnerSetzen } from "@/lib/liveBild";
import { useSelect } from "@/lib/store";
import type { LiveBildAktionen } from "@/lib/useWamo";
import { istOnline, wamo } from "@/lib/wamo";

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
  const titelId = useId();
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
    <section className="lb" aria-labelledby={titelId} data-zustand={zustand}>
      <h2 id={titelId} className="lb-titel">
        Live-Bild des Laptops
      </h2>
      {offen ? <Offen aktionen={aktionen} /> : <Geschlossen aktionen={aktionen} />}
    </section>
  );
});

function Geschlossen({ aktionen }: { aktionen: LiveBildAktionen }) {
  const { text, grund, monitor } = useSelect(wamo, (s) => s.liveBild);
  const erklaerung = text || (grund ? GRUND_TEXT[grund] : undefined);
  return (
    <>
      <p className="lb-text">
        Du siehst den Bildschirm deines eigenen Laptops, solange du das Live-Bild dort im Tray-Menü von WAMO eingeschaltet hast.
        Am Laptop leuchtet dann ein roter Rahmen. Es wird nichts aufgezeichnet oder gespeichert.
      </p>
      {erklaerung && (
        <p className="lb-hinweis" role="status">
          {erklaerung}
        </p>
      )}
      <div className="lb-aktionen">
        <button type="button" className="btn-secondary btn-small" onClick={() => aktionen.an(monitor)}>
          Live-Bild öffnen
        </button>
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
  const hatBild = useRef(false);
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
          hatBild.current = true;
          lupeZeichnen(pos.current);
        }
        bild.close();
      },
      () => {
        const c = flaeche.current;
        c?.getContext("2d")?.clearRect(0, 0, c.width, c.height);
        hatBild.current = false;
      },
    );
    return () => {
      abmelden();
      knoten?.getContext("2d")?.clearRect(0, 0, knoten.width, knoten.height);
    };
  }, [lupeZeichnen]);

  // Pause, Warten, Sperre: kein Bild stehen lassen
  useEffect(() => {
    if (stand.zustand !== "laeuft") liveBildLoeschen();
  }, [stand.zustand]);

  const rest = stand.endeMs ? Math.max(0, Math.round((stand.endeMs - takt.datum) / 1000)) : null;
  const text = stand.text || (stand.grund ? GRUND_TEXT[stand.grund] : "") || ZUSTAND_TEXT[stand.zustand];

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
        <div role="radiogroup" aria-label="Monitor" className="betriebsart-wahl lb-monitore">
          {Array.from({ length: stand.monitore }, (_, i) => i + 1).map((n) => (
            <button
              key={n}
              type="button"
              role="radio"
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
        <button type="button" className="btn-secondary btn-small lb-zu" onClick={() => aktionen.aus()}>
          Live-Bild beenden
        </button>
      </div>
      {stand.monitore < 2 && <p className="lb-klein">Dieser Laptop hat nur einen Monitor, eine Auswahl gibt es nicht.</p>}

      <div
        className="lb-rahmen"
        data-leer={stand.zustand !== "laeuft" ? "" : undefined}
        data-lupe={lupe ? "" : undefined}
        tabIndex={lupe ? 0 : -1}
        aria-label={lupe ? "Bild mit Lupe, Pfeiltasten bewegen die Lupe" : undefined}
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
          hidden={!lupe || !lupePos}
          style={lupePos ? { left: `calc(${lupePos.x * 100}% - ${LUPE_KANTE / 2}px)`, top: `calc(${lupePos.y * 100}% - ${LUPE_KANTE + 12}px)` } : undefined}
        />
      </div>
      <p className="lb-klein">Nichts wird aufgezeichnet. Mit dem Schließen, einer Sperre oder dem Ende des Gesprächs ist das Bild weg.</p>
    </>
  );
}
