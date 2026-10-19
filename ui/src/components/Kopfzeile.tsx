"use client";

import { memo } from "react";
import { ProfilChip } from "@/components/ProfilChip";
import { StatusDetails } from "@/components/Statusleiste";
import { WmcLogo } from "@/components/WmcLogo";

/**
 * Kopfzeile und Statusleiste in einem (48 px): links die Wortmarke WAMO und der Profil-Chip,
 * daneben Verbindung, Betriebsart, Modelle, PC-Host und Mikrofon, rechts das WMC-Logo wie in der
 * WMC-Vorlage. Unter 1024 px bleiben nur Punkte und Kurzformen, die Details liegen im Menü.
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
  return (
    <header className="topbar">
      <h1 className="wordmark-sm">WAMO</h1>
      <ProfilChip email={email} onAbmelden={onAbmelden} />
      <StatusDetails kompakt />
      <div className="topbar-end">
        <button
          type="button"
          className="topbar-btn verlauf-btn"
          aria-expanded={verlaufOffen}
          aria-controls="verlauf"
          onClick={onVerlauf}
        >
          Verlauf
        </button>
        <button type="button" className="topbar-btn menu-btn" popoverTarget="status-menue" aria-label="Status und Menü">
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="M3 5h14M3 10h14M3 15h14" />
          </svg>
        </button>
        <div id="status-menue" popover="auto" className="popover status-pop">
          <p className="eyebrow">Status</p>
          <StatusDetails kompakt={false} />
          <button type="button" className="btn-secondary btn-block" onClick={onVerlauf}>
            {verlaufOffen ? "Verlauf schließen" : "Verlauf öffnen"}
          </button>
        </div>
        <WmcLogo />
      </div>
    </header>
  );
});
