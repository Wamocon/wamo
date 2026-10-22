"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import { ActivityTimeline } from "@/components/ActivityFeed";
import { CanvasBereich } from "@/components/Canvas";
import { Einstellungen } from "@/components/einstellungen/Einstellungen";
import { EINSTELLUNGEN_KNOPF_ID } from "@/components/einstellungen/EinstellungenLink";
import { Auftraege, Freigaben } from "@/components/Freigaben";
import { GemerktHinweis } from "@/components/GemerktHinweis";
import { Bedienung, type TextSenden } from "@/components/kommando/Bedienung";
import { gemerktZurueckgenommen } from "@/components/kommando/gemerktSitzung";
import { Kernbereich } from "@/components/kommando/Kernbereich";
import { KommandoRahmen } from "@/components/kommando/Rahmen";
import { AuftraegeLeer, ErledigtListe, Hinweise, VorgaengeLeer } from "@/components/kommando/Seitenleisten";
import { GeraetEinrichtung } from "@/components/GeraetEinrichtung";
import { Hinweise as AdminHinweise } from "@/components/Hinweise";
import { Kopfzeile } from "@/components/Kopfzeile";
import type { Sitzung } from "@/components/LoginGate";
import { MikrofonWahl } from "@/components/MikrofonWahl";
import { Verlauf } from "@/components/Verlauf";
import { einstellungenSchliessen, useEinstellungenZiel } from "@/lib/einstellungenHash";
import type { Mikrofon } from "@/lib/mikrofon";
import { useModellzugangStandHalten } from "@/lib/modellzugangApi";
import { useSelect } from "@/lib/store";
import type { WamoAktionen } from "@/lib/useWamo";
import { istOnline, wamo, type Memo } from "@/lib/wamo";

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
 * Arbeit, Stand der Einstellungen), in der Mitte der Kern mit Untertiteln, Eingabepille und Canvas, rechts Reiter für
 * Freigaben und Aufträge, Vorgänge (Zeitleiste der Werkzeuge) und Erledigtes. Darunter eine Spalte mit Reiterleiste.
 * Der Verlauf bleibt eine Schublade. Die Einstellungen (components/einstellungen/) sind eine zweite Ansicht derselben
 * Seite, gesteuert über den Hash #einstellungen: Sie verbergen die Kommandozentrale, ohne das Gespräch zu berühren.
 */
export function WamoAnsicht({ aktionen, sitzung }: { aktionen: WamoAktionen; sitzung: Sitzung }) {
  const [verlaufOffen, setVerlaufOffen] = useState(false);
  const einstellungenOffen = useEinstellungenZiel().offen;
  // Verlauf und Einstellungen schließen sich: Der Verlauf liegt über der Kommandozentrale, nicht über den Einstellungen.
  // Beim Öffnen der Einstellungen (Zustand während des Renderns anpassen, kein Zwischenbild) geht der Verlauf zu.
  const [warEinstellungenOffen, setWarEinstellungenOffen] = useState(einstellungenOffen);
  if (warEinstellungenOffen !== einstellungenOffen) {
    setWarEinstellungenOffen(einstellungenOffen);
    if (einstellungenOffen) setVerlaufOffen(false);
  }
  const toggleVerlauf = useCallback(() => {
    if (einstellungenOffen) {
      einstellungenSchliessen();
      setVerlaufOffen(true);
    } else {
      setVerlaufOffen((o) => !o);
    }
  }, [einstellungenOffen]);
  const schliesseVerlauf = useCallback(() => setVerlaufOffen(false), []);
  const { disconnect, connect, vergessen } = aktionen;
  // Der Stand des Modellzugangs wird einmal hier gehalten: die Seitenleiste zeigt eine Zeile daraus, die Einstellungen die Karte
  useModellzugangStandHalten(aktionen.modellzugang);
  // Nach dem Schließen der Einstellungen (Knopf, Escape oder "Zurück" des Browsers) kehrt der Fokus zum Zahnrad zurück
  const warOffen = useRef(false);
  useEffect(() => {
    if (warOffen.current && !einstellungenOffen) document.getElementById(EINSTELLUNGEN_KNOPF_ID)?.focus();
    warOffen.current = einstellungenOffen;
  }, [einstellungenOffen]);
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
          <AdminHinweise />
          <GeraetEinrichtung />
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
      hinweise={<Hinweise />}
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
      einstellungen={
        einstellungenOffen ? (
          <Einstellungen aktionen={aktionen} email={sitzung.email} onAbmelden={abmelden} toast={<HinweisToast />} />
        ) : null
      }
    />
  );
}
