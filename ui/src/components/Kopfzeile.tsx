"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import { AbmeldenRueckfrage } from "@/components/AbmeldenRueckfrage";
import { EinstellungenKnopf } from "@/components/einstellungen/EinstellungenLink";
import { Statusanzeige } from "@/components/kommando/Statusanzeige";
import { ProfilChip } from "@/components/ProfilChip";
import { StatusDetails } from "@/components/Statusleiste";
import { ThemaSchalter } from "@/components/ThemaSchalter";
import { WmcLogo } from "@/components/WmcLogo";
import { laborAnsicht } from "@/lib/laborPlatz";

/**
 * Kopfzeile und Statusleiste in einem (48 px): links die Wortmarke WAMO und der Profil-Chip,
 * daneben Verbindung, Betriebsart, Modelle, PC-Host und Mikrofon, rechts das WMC-Logo wie in der
 * WMC-Vorlage. Unter 1024 px bleiben nur Punkte und Kurzformen, die Details liegen im Menü.
 * Rechts davor die Statusanzeige der Kommandozentrale: Sitzungsfortschritt, ONLINE-Abzeichen, Uhrzeit, dann das
 * Zahnrad zu den Einstellungen (ein Link auf #einstellungen, auch mit der Tastatur erreichbar).
 * Laborkonten (lib/laborPlatz.ts): Überschrift "WAMO Testlabor, Platz N", kein Verlauf, und "Abmelden" fragt erst nach
 * (components/AbmeldenRueckfrage.tsx). Für alle anderen Konten bleibt alles wie es war.
 */
export const Kopfzeile = memo(function Kopfzeile({
  email,
  onAbmelden,
  verlaufOffen,
  onVerlauf,
}: {
  email: string | null;
  onAbmelden: () => void;
  verlaufOffen: boolean;
  onVerlauf: () => void;
}) {
  const ansicht = laborAnsicht(email);
  const kopf = useRef<HTMLElement>(null);
  const [frage, setFrage] = useState(false);
  // Nach einem Abbruch kehrt der Fokus zum Profil-Chip zurück, aber erst wenn der Dialog aus dem Dokument ist (siehe AbmeldenRueckfrage)
  const zurueck = useRef(false);
  useEffect(() => {
    if (frage || !zurueck.current) return;
    zurueck.current = false;
    kopf.current?.querySelector<HTMLElement>(".profile-chip")?.focus();
  }, [frage]);

  const abmeldenAnfragen = useCallback(() => {
    // das Profil-Popover liegt über der Seite: erst zu, dann die Frage (das Popover würde sonst beim ersten Klick in den Dialog zufallen)
    try {
      kopf.current?.querySelector<HTMLElement>(".profile-pop")?.hidePopover?.();
    } catch {
      // schon zu oder Popover nicht unterstützt: nichts zu tun
    }
    setFrage(true);
  }, []);
  const abbrechen = useCallback(() => {
    zurueck.current = true;
    setFrage(false);
  }, []);
  const bestaetigen = useCallback(() => {
    setFrage(false);
    onAbmelden();
  }, [onAbmelden]);

  return (
    <header className="topbar" ref={kopf}>
      <h1 className="wordmark-sm" data-labor={ansicht.kopfTitel ? "" : undefined} title={ansicht.kopfTitel ?? undefined}>
        {ansicht.kopfTitel ?? "WAMO"}
      </h1>
      <ProfilChip email={email} onAbmelden={ansicht.abmeldenFragen ? abmeldenAnfragen : onAbmelden} />
      <StatusDetails kompakt stimme={ansicht.stimme} />
      <div className="topbar-end">
        <Statusanzeige />
        <ThemaSchalter />
        <EinstellungenKnopf />
        {ansicht.verlauf && (
          <button
            type="button"
            className="topbar-btn verlauf-btn"
            aria-expanded={verlaufOffen}
            aria-controls="verlauf"
            onClick={onVerlauf}
          >
            Verlauf
          </button>
        )}
        <button type="button" className="topbar-btn menu-btn" popoverTarget="status-menue" aria-label="Status und Menü">
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="M3 5h14M3 10h14M3 15h14" />
          </svg>
        </button>
        <div id="status-menue" popover="auto" className="popover status-pop">
          <p className="eyebrow">Status</p>
          <StatusDetails kompakt={false} stimme={ansicht.stimme} />
          {ansicht.verlauf && (
            <button type="button" className="btn-secondary btn-block" onClick={onVerlauf}>
              {verlaufOffen ? "Verlauf schließen" : "Verlauf öffnen"}
            </button>
          )}
        </div>
        <WmcLogo />
      </div>
      {frage && <AbmeldenRueckfrage onBestaetigt={bestaetigen} onAbbruch={abbrechen} />}
    </header>
  );
});
