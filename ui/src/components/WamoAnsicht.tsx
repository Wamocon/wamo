"use client";

import { memo, useCallback, useState } from "react";
import { ActivityTimeline, LABEL } from "@/components/ActivityFeed";
import { CanvasBereich } from "@/components/Canvas";
import { Auftraege, Freigaben } from "@/components/Freigaben";
import { GemerktHinweis } from "@/components/GemerktHinweis";
import { Kopfzeile } from "@/components/Kopfzeile";
import { LiveCaption } from "@/components/LiveCaption";
import type { Sitzung } from "@/components/LoginGate";
import { Orb } from "@/components/Orb";
import { Verlauf } from "@/components/Verlauf";
import { VoiceLine } from "@/components/VoiceLine";
import { useSelect } from "@/lib/store";
import type { WamoAktionen } from "@/lib/useWamo";
import {
  historyRef,
  istOnline,
  laufendesWerkzeug,
  levelsRef,
  wamo,
  wartetAufEntscheidung,
  type Presence,
} from "@/lib/wamo";

const PRESENCE_TEXT: Record<Presence, string> = {
  offline: "schläft",
  connecting: "wacht auf",
  idle: "ist da",
  listening: "hört zu",
  thinking: "denkt nach",
  speaking: "spricht",
};

/** Kern, Zustandswort und Untertitel: die Präsenz. Unter 1024 px als schmale, klebende Leiste. */
const Praesenz = memo(function Praesenz() {
  const presence = useSelect(wamo, (s) => s.presence);
  const werkzeug = useSelect(wamo, (s) => (istOnline(s) ? laufendesWerkzeug(s) : null));
  const freigabe = useSelect(wamo, wartetAufEntscheidung);
  const zustand = freigabe
    ? "wartet auf deine Entscheidung"
    : werkzeug
      ? `handelt: ${LABEL[werkzeug] ?? werkzeug}`
      : PRESENCE_TEXT[presence];
  return (
    <section className="presence-core" data-presence={presence} aria-label="Gesprächszustand">
      <div className="kern-wrap">
        <Orb levels={levelsRef} />
      </div>
      <div className="presence-text">
        <p className="state" data-presence={presence} data-zustand={freigabe ? "freigabe" : werkzeug ? "handelt" : presence}>
          WAMO {zustand}
        </p>
        <LiveCaption />
      </div>
    </section>
  );
});

/** Fehler in Tinte mit rotem Symbol und sicherer nächster Aktion. */
const FehlerAnzeige = memo(function FehlerAnzeige({
  onRetry,
  onAbmelden,
  onSchliessen,
}: {
  onRetry: () => void;
  onAbmelden: () => void;
  onSchliessen: () => void;
}) {
  const error = useSelect(wamo, (s) => s.error);
  const offline = useSelect(wamo, (s) => s.presence === "offline");
  if (!error) return null;
  const hinweis = error.art === "hinweis";
  const retry = offline && !hinweis && error.art !== "anmeldung";
  return (
    <div className="error" role={hinweis ? "status" : "alert"} data-art={error.art}>
      <svg className="sym sym-warn" viewBox="0 0 16 16" aria-hidden="true">
        <rect x="1" y="1" width="14" height="14" rx="2" />
        <path d="M8 4v5M8 11.2v.6" />
      </svg>
      <div className="error-body">
        <p>{error.text}</p>
        <div className="error-actions">
          {retry && (
            <button type="button" className="btn-secondary btn-small" onClick={onRetry}>
              Erneut verbinden
            </button>
          )}
          {error.art === "anmeldung" && (
            <button type="button" className="btn-secondary btn-small" onClick={onAbmelden}>
              Abmelden
            </button>
          )}
          <button type="button" className="error-close" onClick={onSchliessen}>
            Schließen
          </button>
        </div>
      </div>
    </div>
  );
});

/** Hauptschaltfläche und Mikrofon (unter 1024 px unten fixiert). */
const Aktionen = memo(function Aktionen({ aktionen }: { aktionen: WamoAktionen }) {
  const presence = useSelect(wamo, (s) => s.presence);
  const online = useSelect(wamo, istOnline);
  const micOn = useSelect(wamo, (s) => s.micOn);
  return (
    <div className="actions">
      {presence === "connecting" ? (
        <>
          <button type="button" className="btn-primary wake" disabled>
            Verbinde …
          </button>
          <button type="button" className="btn-secondary" onClick={() => void aktionen.disconnect()}>
            Abbrechen
          </button>
        </>
      ) : (
        <>
          {online ? (
            <button type="button" className="btn-secondary wake" onClick={() => void aktionen.disconnect()}>
              Gespräch beenden
            </button>
          ) : (
            <button type="button" className="btn-primary wake" onClick={() => void aktionen.connect()}>
              Gespräch starten
            </button>
          )}
          <button
            type="button"
            className="btn-mic"
            aria-pressed={!micOn}
            aria-keyshortcuts="Control+M"
            disabled={!online}
            title="Mikrofon stumm schalten (Strg+M)"
            onClick={aktionen.toggleMic}
          >
            <svg viewBox="0 0 20 20" aria-hidden="true">
              <rect x="7" y="2.5" width="6" height="10" rx="3" />
              <path d="M4.5 9.5a5.5 5.5 0 0 0 11 0M10 15v2.5" />
              {!micOn && <path className="mic-off" d="M3 3l14 14" />}
            </svg>
            Stumm
          </button>
        </>
      )}
    </div>
  );
});

/**
 * Schalter "PC-Steuerung" für diese Sitzung: Standard aus, nur für admin und nur bei Verbindung.
 * Gesendet wird nur der Wunsch; was gilt, meldet der Kern in betrieb.pc_steuerung zurück.
 */
const PcSteuerungSchalter = memo(function PcSteuerungSchalter({
  onSchalten,
}: {
  onSchalten: (an: boolean) => boolean;
}) {
  const admin = useSelect(wamo, (s) => s.profil?.rolle === "admin");
  const online = useSelect(wamo, istOnline);
  const an = useSelect(wamo, (s) => s.betrieb?.pcSteuerung === "an");
  const [ziel, setZiel] = useState<boolean | null>(null);
  const ausstehend = ziel !== null && ziel !== an;
  if (!admin || !online) return null;
  return (
    <div className="pc-switch-row">
      <button
        type="button"
        role="switch"
        aria-checked={an}
        className="pc-switch"
        data-an={an || undefined}
        disabled={ausstehend}
        onClick={() => {
          const neu = !an;
          if (!onSchalten(neu)) return;
          setZiel(neu);
          setTimeout(() => setZiel(null), 5000); // ohne Rückmeldung nach 5 s wieder bedienbar
        }}
      >
        <span className="pc-switch-bahn" aria-hidden="true">
          <span className="pc-switch-knopf" />
        </span>
        PC-Steuerung {an ? "an" : "aus"}
      </button>
      <span className="pc-switch-hint">{ausstehend ? "wird geschaltet …" : "gilt nur für diese Sitzung"}</span>
    </div>
  );
});

/** role="status" nur für bedeutsame Wechsel (verbunden, getrennt, Mikrofon, PC). */
const Ansage = memo(function Ansage() {
  const ansage = useSelect(wamo, (s) => s.ansage);
  return (
    <p className="sr-only" role="status">
      {ansage}
    </p>
  );
});

/**
 * Hauptansicht. Abschnitte abonnieren eigene Store-Ausschnitte, deshalb rendert hier nichts
 * im Worttakt neu. Aufbau ab 1280 px: Kopfzeile, links die Präsenzspalte (Freigaben, Fehler,
 * Kern, Untertitel, Stimmlinie, Gemerkt, Werkzeuge, Schaltflächen), rechts das Canvas;
 * ab 1600 px dritte Spalte Verlauf, darunter als Schublade. Unter 1024 px eine Spalte mit
 * klebender Präsenzleiste und unten fixierten Schaltflächen.
 */
export function WamoAnsicht({ aktionen, sitzung }: { aktionen: WamoAktionen; sitzung: Sitzung }) {
  const [verlaufOffen, setVerlaufOffen] = useState(false);
  const toggleVerlauf = useCallback(() => setVerlaufOffen((o) => !o), []);
  const schliesseVerlauf = useCallback(() => setVerlaufOffen(false), []);
  const { disconnect, connect } = aktionen;
  const { signOut } = sitzung;
  const abmelden = useCallback(() => {
    void disconnect();
    signOut();
  }, [disconnect, signOut]);
  const retry = useCallback(() => void connect(), [connect]);

  return (
    <div className="shell">
      <Kopfzeile email={sitzung.email} onAbmelden={abmelden} verlaufOffen={verlaufOffen} onVerlauf={toggleVerlauf} />
      <main className="workspace">
        <div className="col-main">
          <Freigaben onDecide={aktionen.decide} />
          <Auftraege onDecide={aktionen.entscheideAuftrag} />
          <FehlerAnzeige onRetry={retry} onAbmelden={abmelden} onSchliessen={aktionen.schliesseFehler} />
          <Praesenz />
          <div className="voice">
            <VoiceLine history={historyRef} />
            <p className="legend" aria-hidden="true">
              <span className="legend-user">Du</span>
              <span className="legend-bot">WAMO</span>
            </p>
          </div>
          <GemerktHinweis onVergessen={aktionen.vergessen} />
          <ActivityTimeline />
          <PcSteuerungSchalter onSchalten={aktionen.pcSteuerung} />
          <Aktionen aktionen={aktionen} />
        </div>
        <section className="canvas" aria-label="Canvas">
          <CanvasBereich />
        </section>
        <Verlauf offen={verlaufOffen} onSchliessen={schliesseVerlauf} />
      </main>
      <Ansage />
    </div>
  );
}
