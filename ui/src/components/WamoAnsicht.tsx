"use client";

import { memo, useCallback, useEffect, useState } from "react";
import { ActivityTimeline } from "@/components/ActivityFeed";
import { ModellZugang } from "@/components/ModellZugang";
import { CanvasBereich } from "@/components/Canvas";
import { Auftraege, Freigaben } from "@/components/Freigaben";
import { GemerktHinweis } from "@/components/GemerktHinweis";
import { Bedienung, type TextSenden } from "@/components/kommando/Bedienung";
import { gemerktZurueckgenommen } from "@/components/kommando/gemerktSitzung";
import { Kernbereich } from "@/components/kommando/Kernbereich";
import { KommandoRahmen } from "@/components/kommando/Rahmen";
import { AuftraegeLeer, ErledigtListe, Hinweise, VorgaengeLeer } from "@/components/kommando/Seitenleisten";
import { Kopfzeile } from "@/components/Kopfzeile";
import type { Sitzung } from "@/components/LoginGate";
import { StatusDetails } from "@/components/Statusleiste";
import { Verlauf } from "@/components/Verlauf";
import {
  BETRIEBSARTEN,
  BUERO_VERFUEGBAR,
  betriebsartSetzen,
  KERN,
  useBetriebsart,
  type Betriebsart,
} from "@/lib/betriebsart";
import { pcAutoSetzen, usePcAuto } from "@/lib/einstellungen";
import { useServerBrowser } from "@/lib/geraet";
import type { Mikrofon } from "@/lib/mikrofon";
import { useSelect } from "@/lib/store";
import type { WamoAktionen } from "@/lib/useWamo";
import { istOnline, wamo, zeigeToast, type Memo } from "@/lib/wamo";

/** Gerätewahl in der Mikrofon-Warnung: Liste beim Erscheinen laden, Wechsel gilt sofort und auf diesem Gerät. */
function MikrofonWahl({ laden, waehlen }: { laden: () => Promise<Mikrofon[]>; waehlen: (id: string) => boolean }) {
  const [liste, setListe] = useState<Mikrofon[] | null>(null);
  const aktuell = useSelect(wamo, (s) => s.mikrofon);
  useEffect(() => {
    let weg = false;
    void laden().then((l) => {
      if (!weg) setListe(l);
    });
    return () => {
      weg = true;
    };
  }, [laden]);
  if (!liste || liste.length === 0) return null;
  return (
    <label className="mic-wahl">
      <span>Anderes Mikrofon:</span>
      <select defaultValue="" onChange={(e) => e.target.value && waehlen(e.target.value)}>
        <option value="" disabled>
          {aktuell ? `jetzt: ${aktuell}` : "wählen …"}
        </option>
        {liste.map((m) => (
          <option key={m.id} value={m.id}>
            {m.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/** Fehler in Tinte mit rotem Symbol und sicherer nächster Aktion. */
const FehlerAnzeige = memo(function FehlerAnzeige({
  onRetry,
  onAbmelden,
  onSchliessen,
  onMikrofone,
  onMikrofonWaehlen,
}: {
  onRetry: () => void;
  onAbmelden: () => void;
  onSchliessen: () => void;
  onMikrofone: () => Promise<Mikrofon[]>;
  onMikrofonWaehlen: (id: string) => boolean;
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
          {error.art === "mikrofon" && <MikrofonWahl laden={onMikrofone} waehlen={onMikrofonWaehlen} />}
          <button type="button" className="error-close" onClick={onSchliessen}>
            Schließen
          </button>
        </div>
      </div>
    </div>
  );
});

/** Hauptschaltfläche (unter 1100 px unten fixiert). Das Mikrofon sitzt in der Eingabepille (kommando/Bedienung.tsx). */
const Aktionen = memo(function Aktionen({ aktionen }: { aktionen: WamoAktionen }) {
  const presence = useSelect(wamo, (s) => s.presence);
  const online = useSelect(wamo, istOnline);
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
  const belegt = useSelect(wamo, (s) => s.betrieb?.pcBelegt === true && s.betrieb?.pcSteuerung !== "an");
  const pcName = useSelect(wamo, (s) => s.betrieb?.pcName ?? null);
  const [ziel, setZiel] = useState<boolean | null>(null);
  const auto = usePcAuto();
  const serverBrowser = useServerBrowser();
  const ausstehend = ziel !== null && ziel !== an && !belegt;
  if (!admin) return null;
  if (serverBrowser) {
    // Browser auf dem Server: die PC-Werkzeuge gehören zur Desktop-App (lib/geraet.ts)
    return (
      <div className="pc-steuerung">
        <p className="pc-switch-hint">PC-Steuerung: nur in der Desktop-App verfügbar</p>
      </div>
    );
  }
  const hinweis = ausstehend
    ? "wird geschaltet …"
    : belegt
      ? "eine andere Sitzung steuert diesen PC gerade"
      : `gilt nur für diese Sitzung${pcName ? `, Ziel ${pcName}` : ""}`;
  return (
    <div className="pc-steuerung">
      {online && (
        <div className="pc-switch-row">
          <PcSchalterKnopf an={an} ausstehend={ausstehend} onSchalten={onSchalten} setZiel={setZiel} />
          <span className="pc-switch-hint">{hinweis}</span>
        </div>
      )}
      <label className="pc-auto">
        <input type="checkbox" checked={auto} onChange={(e) => pcAutoSetzen(e.target.checked)} />
        <span>PC-Steuerung beim Start automatisch einschalten (dieses Gerät)</span>
      </label>
      {auto && <p className="pc-auto-hint">Ohne Sprecherprüfung reagiert WAMO dann auf jede Stimme im Raum.</p>}
    </div>
  );
});

function PcSchalterKnopf({
  an,
  ausstehend,
  onSchalten,
  setZiel,
}: {
  an: boolean;
  ausstehend: boolean;
  onSchalten: (an: boolean) => boolean;
  setZiel: (ziel: boolean | null) => void;
}) {
  return (
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
  );
}

/**
 * Betriebsart dieses Geräts: Büro (Kern auf Sokrates-2) oder Unterwegs (Kern auf diesem PC). Umschalten nur ohne
 * laufendes Gespräch; es gilt ab dem nächsten Verbinden. In der Desktop-App startet "Unterwegs" den lokalen Kern.
 */
const BetriebsartWahl = memo(function BetriebsartWahl() {
  const art = useBetriebsart();
  const offline = useSelect(wamo, (s) => s.presence === "offline");
  const [laeuft, setLaeuft] = useState(false);
  const serverBrowser = useServerBrowser();
  if (!BUERO_VERFUEGBAR || serverBrowser) return null; // Browser auf dem Server: nur Büro, nichts zu wählen
  const waehle = (neu: Betriebsart) => {
    if (neu === art || !offline || laeuft) return;
    setLaeuft(true);
    void betriebsartSetzen(neu)
      .then((gilt) => zeigeToast(`Betriebsart ${KERN[gilt].name}: Kern ${KERN[gilt].ort}`))
      .catch(() => zeigeToast("Betriebsart konnte nicht umgeschaltet werden"))
      .finally(() => setLaeuft(false));
  };
  return (
    <div className="betriebsart">
      <div className="betriebsart-wahl" role="radiogroup" aria-label="Betriebsart">
        {BETRIEBSARTEN.map((a) => (
          <button
            key={a}
            type="button"
            role="radio"
            aria-checked={a === art}
            data-an={a === art || undefined}
            disabled={!offline || laeuft}
            onClick={() => waehle(a)}
          >
            {KERN[a].name}
          </button>
        ))}
      </div>
      <span className="pc-switch-hint">
        Kern: {KERN[art].ort}
        {!offline && " (umschalten nach dem Gespräch)"}
      </span>
    </div>
  );
});

/** Kurzer sichtbarer Hinweis (z. B. Autostart der PC-Steuerung); die Ansage läuft über role="status". */
const HinweisToast = memo(function HinweisToast() {
  const toast = useSelect(wamo, (s) => s.toast);
  if (!toast) return null;
  return (
    <p className="hinweis-toast" key={toast.key}>
      <span className="dot dot-rot" aria-hidden="true" />
      {toast.text}
    </p>
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
 * Optionale Erweiterung von WamoAktionen: Text an WAMO senden (Texteingabe der Eingabepille).
 * Bietet useWamo die Aktion an, wird die Eingabepille ohne weitere Änderung aktiv.
 */
type MitText = WamoAktionen & { textSenden?: TextSenden };

/**
 * Hauptansicht "Kommandozentrale" (kommando/Rahmen.tsx). Abschnitte abonnieren eigene Store-Ausschnitte,
 * deshalb rendert hier nichts im Worttakt neu. Ab 1100 px drei Spalten: links Hinweise (Gedächtnis, geteilte
 * Arbeit, Geräte), in der Mitte der Kern mit Untertiteln, Eingabepille und Canvas, rechts Reiter für Freigaben
 * und Aufträge, Vorgänge (Zeitleiste der Werkzeuge) und Erledigtes. Darunter eine Spalte mit Reiterleiste.
 * Der Verlauf bleibt eine Schublade.
 */
export function WamoAnsicht({ aktionen, sitzung }: { aktionen: WamoAktionen; sitzung: Sitzung }) {
  const [verlaufOffen, setVerlaufOffen] = useState(false);
  const toggleVerlauf = useCallback(() => setVerlaufOffen((o) => !o), []);
  const schliesseVerlauf = useCallback(() => setVerlaufOffen(false), []);
  const { disconnect, connect, vergessen } = aktionen;
  // "Rückgängig" nimmt den Eintrag auch aus der Gedächtnis-Karte, sobald er aus dem Store verschwunden ist
  const vergesseEintrag = useCallback(
    (m: Memo) => {
      vergessen(m);
      gemerktZurueckgenommen(m);
    },
    [vergessen],
  );
  const { signOut } = sitzung;
  const abmelden = useCallback(() => {
    void disconnect();
    signOut();
  }, [disconnect, signOut]);
  const retry = useCallback(() => void connect(), [connect]);

  return (
    <KommandoRahmen
      kopf={<Kopfzeile email={sitzung.email} onAbmelden={abmelden} verlaufOffen={verlaufOffen} onVerlauf={toggleVerlauf} />}
      meldungen={
        <>
          <FehlerAnzeige
            onRetry={retry}
            onAbmelden={abmelden}
            onSchliessen={aktionen.schliesseFehler}
            onMikrofone={aktionen.mikrofone}
            onMikrofonWaehlen={aktionen.mikrofonWaehlen}
          />
          <HinweisToast />
          <GemerktHinweis onVergessen={vergesseEintrag} />
        </>
      }
      kernbereich={<Kernbereich />}
      aktionen={<Aktionen aktionen={aktionen} />}
      bedienung={
        <Bedienung
          onMikro={aktionen.toggleMic}
          senden={(aktionen as MitText).textSenden}
          verlaufOffen={verlaufOffen}
          onVerlauf={toggleVerlauf}
        />
      }
      canvas={<CanvasBereich />}
      hinweise={
        <Hinweise
          geraete={
            <>
              <StatusDetails kompakt={false} />
              <PcSteuerungSchalter onSchalten={aktionen.pcSteuerung} />
              <BetriebsartWahl />
              <ModellZugang onAnfrage={aktionen.modellzugang} />
            </>
          }
        />
      }
      auftraege={
        <>
          <Freigaben onDecide={aktionen.decide} />
          <Auftraege onDecide={aktionen.entscheideAuftrag} />
          <AuftraegeLeer />
        </>
      }
      vorgaenge={
        <>
          <ActivityTimeline />
          <VorgaengeLeer />
        </>
      }
      erledigt={<ErledigtListe />}
      anhang={
        <>
          <Verlauf offen={verlaufOffen} onSchliessen={schliesseVerlauf} />
          <Ansage />
        </>
      }
    />
  );
}
