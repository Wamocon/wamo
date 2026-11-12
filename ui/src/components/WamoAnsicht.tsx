"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import { ActivityTimeline } from "@/components/ActivityFeed";
import { BildschirmAnsicht } from "@/components/BildschirmAnsicht";
import { CanvasBereich } from "@/components/Canvas";
import { Einstellungen } from "@/components/einstellungen/Einstellungen";
import { EINSTELLUNGEN_KNOPF_ID } from "@/components/einstellungen/EinstellungenLink";
import { Auftraege, Freigaben } from "@/components/Freigaben";
import { GemerktHinweis } from "@/components/GemerktHinweis";
import { Bedienung } from "@/components/kommando/Bedienung";
import { BueroKarte } from "@/components/kommando/BueroKarte";
import { useBueroStandHalten } from "@/components/kommando/useBueroStand";
import { gemerktZurueckgenommen } from "@/components/kommando/gemerktSitzung";
import { Kernbereich } from "@/components/kommando/Kernbereich";
import { KommandoRahmen } from "@/components/kommando/Rahmen";
import { AuftraegeLeer, ErledigtListe, Hinweise, VorgaengeLeer } from "@/components/kommando/Seitenleisten";
import { VorgaengeTafel } from "@/components/kommando/Vorgaenge";
import { GeraetEinrichtung, GeraetZeile } from "@/components/GeraetEinrichtung";
import { Hinweise as AdminHinweise, HinweiseZaehler } from "@/components/Hinweise";
import { Kopfzeile } from "@/components/Kopfzeile";
import type { Sitzung } from "@/components/LoginGate";
import { FehlerAnzeige } from "@/components/FehlerAnzeige";
import { StillAktionen, UebernehmenHinweis } from "@/components/StillStart";
import { Verlauf } from "@/components/Verlauf";
import { einstellungenSchliessen, oeffnerFuerFokus, useEinstellungenZiel } from "@/lib/einstellungenHash";
import { laborAnsicht } from "@/lib/laborPlatz";
import { useModellzugangStandHalten } from "@/lib/modellzugangApi";
import { useSelect } from "@/lib/store";
import type { WamoAktionen } from "@/lib/useWamo";
import { useVerlaufHalten } from "@/lib/vorgaengeApi";
import { istOnline, wamo, type Memo } from "@/lib/wamo";

/** Hauptschaltfläche (unter 1100 px unten fixiert). Das Mikrofon sitzt in der Eingabepille (kommando/Bedienung.tsx). */
const Aktionen = memo(function Aktionen({ aktionen }: { aktionen: WamoAktionen }) {
  const presence = useSelect(wamo, (s) => s.presence);
  const online = useSelect(wamo, istOnline);
  const still = useSelect(wamo, (s) => s.stillStart.still);
  return (
    <div className="actions">
      {online && still && aktionen.sprechen ? (
        // Start beim Tippen: "Gespräch starten" macht die stille Sitzung zum Gespräch (keine neue Sitzung, keine Verdrängung)
        <StillAktionen onSprechen={aktionen.sprechen} onBeenden={() => void aktionen.disconnect()} />
      ) : presence === "connecting" ? (
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
 * Hauptansicht "Kommandozentrale" (kommando/Rahmen.tsx). Abschnitte abonnieren eigene Store-Ausschnitte,
 * deshalb rendert hier nichts im Worttakt neu. Ab 1100 px drei Spalten: links Hinweise (Gedächtnis, geteilte
 * Arbeit, Stand der Einstellungen), in der Mitte der Kern mit Untertiteln, Eingabepille und Canvas, rechts Reiter für
 * Freigaben und Aufträge, Vorgänge (Karten mit Livefortschritt, darunter die Zeitleiste der Werkzeuge) und Erledigtes. Darunter
 * eine Spalte mit Reiterleiste.
 * Der Verlauf bleibt eine Schublade. Die Einstellungen (components/einstellungen/) sind eine zweite Ansicht derselben
 * Seite, gesteuert über den Hash #einstellungen: Sie verbergen die Kommandozentrale, ohne das Gespräch zu berühren.
 * Laborkonten (lib/laborPlatz.ts) sehen eine schlanke Platz-Ansicht: ohne Büro-Karte, Verlauf, Vorgänge, Admin-Hinweise,
 * Geräteeinrichtung und Modellzugang. Das ist nur Darstellung, der Kern sperrt weiter selbst.
 */
export function WamoAnsicht({ aktionen, sitzung }: { aktionen: WamoAktionen; sitzung: Sitzung }) {
  const ansicht = laborAnsicht(sitzung.email);
  const [verlaufOffen, setVerlaufOffen] = useState(false);
  const einstellungenOffen = useEinstellungenZiel().offen;
  // Beim Öffnen der Einstellungen geht der Verlauf zu (Zustand während des Renderns anpassen, kein Zwischenbild); danach
  // lässt er sich auch über den Einstellungen öffnen, denn er liegt als Schublade über der ganzen Seite.
  const [warEinstellungenOffen, setWarEinstellungenOffen] = useState(einstellungenOffen);
  if (warEinstellungenOffen !== einstellungenOffen) {
    setWarEinstellungenOffen(einstellungenOffen);
    if (einstellungenOffen) setVerlaufOffen(false);
  }
  const toggleVerlauf = useCallback(() => setVerlaufOffen((o) => !o), []);
  const schliesseVerlauf = useCallback(() => setVerlaufOffen(false), []);
  const { disconnect, connect, vergessen } = aktionen;
  // Der Stand des Modellzugangs wird einmal hier gehalten: die Seitenleiste zeigt eine Zeile daraus, die Einstellungen die Karte
  // (Laborkonten: kein Abruf. Die drei Hooks stehen in jedem Render in gleicher Reihenfolge, nur ihr Parameter ändert sich.)
  useModellzugangStandHalten(aktionen.modellzugang, ansicht.modellzugang);
  // Der Verlauf der Vorgänge (Datenbank) wird hier einmal geholt: die Tafel hängt nur bei sichtbarem Reiter ein
  useVerlaufHalten(ansicht.verlauf);
  // Der Stand der Büro-Karte gehört dem angemeldeten Profil: beim Abmelden (Aushängen dieser Ansicht) wird er vergessen
  useBueroStandHalten(ansicht.bueroKarte);
  // Nach dem Schließen der Einstellungen (Knopf, Escape oder "Zurück" des Browsers) kehrt der Fokus zu dem zurück, was
  // sie geöffnet hat (Zahnrad oder Link), sonst zum Zahnrad. Hat die Person den Fokus schon selbst gesetzt (zum Beispiel
  // auf "Verlauf"), bleibt er dort.
  const warOffen = useRef(false);
  useEffect(() => {
    if (warOffen.current && !einstellungenOffen) {
      const a = document.activeElement;
      if (!a || a === document.body) (oeffnerFuerFokus() ?? document.getElementById(EINSTELLUNGEN_KNOPF_ID))?.focus();
    }
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
  // "Hier übernehmen" nach einer Übernahme durch ein anderes Fenster: erst dieser Klick verdrängt dort die Sitzung (still, wie beim Tippen)
  const uebernehmen = useCallback(() => void connect({ still: true }), [connect]);

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
          <UebernehmenHinweis onUebernehmen={uebernehmen} />
          <GemerktHinweis onVergessen={vergesseEintrag} />
          {ansicht.adminHinweise && <AdminHinweise />}
          {ansicht.geraeteEinrichtung && <GeraetEinrichtung />}
        </>
      }
      kernbereich={<Kernbereich />}
      aktionen={<Aktionen aktionen={aktionen} />}
      bedienung={
        <Bedienung
          onMikro={aktionen.toggleMic}
          senden={aktionen.textSenden}
          stillStarten={aktionen.stillStarten}
          eingabeMelden={aktionen.eingabeMelden}
          verlaufOffen={verlaufOffen}
          onVerlauf={ansicht.verlauf ? toggleVerlauf : undefined}
        />
      }
      canvas={
        <>
          {aktionen.liveBild && <BildschirmAnsicht aktionen={aktionen.liveBild} />}
          <CanvasBereich />
        </>
      }
      hinweise={<Hinweise modellzugang={ansicht.modellzugang} />}
      auftraege={
        <>
          <Freigaben onDecide={aktionen.decide} />
          <Auftraege onDecide={aktionen.entscheideAuftrag} />
          <AuftraegeLeer />
        </>
      }
      vorgaenge={
        ansicht.vorgaenge ? (
          <>
            <VorgaengeTafel />
            <ActivityTimeline />
            <VorgaengeLeer />
          </>
        ) : null
      }
      erledigt={<ErledigtListe />}
      buero={ansicht.bueroKarte ? <BueroKarte /> : undefined}
      ohne={{ vorgaenge: !ansicht.vorgaenge, buero: !ansicht.bueroKarte }}
      anhang={
        <>
          {ansicht.verlauf && <Verlauf offen={verlaufOffen} onSchliessen={schliesseVerlauf} />}
          <Ansage />
        </>
      }
      einstellungen={
        einstellungenOffen ? (
          <Einstellungen
            aktionen={aktionen}
            email={sitzung.email}
            onAbmelden={abmelden}
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
                {ansicht.adminHinweise && <HinweiseZaehler onAnsehen={() => einstellungenSchliessen()} />}
                {ansicht.geraeteEinrichtung && <GeraetZeile />}
              </>
            }
          />
        ) : null
      }
    />
  );
}
