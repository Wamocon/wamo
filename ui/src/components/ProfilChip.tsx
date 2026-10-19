"use client";

import { memo } from "react";
import type { Profil } from "@/lib/nachrichten";
import { useSelect } from "@/lib/store";
import { wamo } from "@/lib/wamo";

const ROLLE_TEXT: Record<Profil["rolle"], string> = { admin: "Admin", mitglied: "Mitglied" };

function kuerzel(name: string) {
  const teile = name.trim().split(/[\s.@_-]+/).filter(Boolean);
  const k = teile.length > 1 ? teile[0][0] + teile[1][0] : (teile[0] ?? "?").slice(0, 1);
  return k.toUpperCase();
}

/**
 * Profil-Chip: Kürzel im schwarzen Kreis, Vorname und Rolle als kleines Kennzeichen.
 * Das Popover (HTML-Popover-API, keine Abhängigkeit) zeigt Name, E-Mail, Rolle und "Abmelden";
 * Profil wechseln heißt abmelden und neu anmelden. Bis die Meldung "profil" vom Kern kommt,
 * steht die Anmelde-E-Mail da.
 */
export const ProfilChip = memo(function ProfilChip({
  email,
  onAbmelden,
}: {
  email: string | null;
  onAbmelden: () => void;
}) {
  const profil = useSelect(wamo, (s) => s.profil);
  const anzeige = profil?.name ?? email ?? "Profil";
  const vorname = profil ? profil.name.split(/\s+/)[0] : email ? email.split("@")[0] : "Profil";

  return (
    <div className="profile">
      <button
        type="button"
        className="profile-chip"
        popoverTarget="profil-popover"
        aria-label={`Profil ${anzeige}${profil ? `, ${ROLLE_TEXT[profil.rolle]}` : ""}`}
      >
        <span className="initials" aria-hidden="true">
          {kuerzel(anzeige)}
        </span>
        <span className="profile-first" aria-hidden="true">
          {vorname}
        </span>
        {profil && (
          <span className="role-tag" data-rolle={profil.rolle} aria-hidden="true">
            {ROLLE_TEXT[profil.rolle]}
          </span>
        )}
      </button>
      <div id="profil-popover" popover="auto" className="popover profile-pop">
        <p className="eyebrow">Angemeldet</p>
        <p className="pop-name">{profil?.name ?? "Profil noch unbekannt"}</p>
        {email && <p className="pop-mail">{email}</p>}
        {profil && (
          <p className="pop-role">
            Rolle <span className="role-tag" data-rolle={profil.rolle}>{ROLLE_TEXT[profil.rolle]}</span>
          </p>
        )}
        <button type="button" className="btn-secondary btn-block" onClick={onAbmelden}>
          Abmelden
        </button>
        <p className="pop-hint">Zum Profilwechsel abmelden und mit dem anderen Profil anmelden.</p>
      </div>
    </div>
  );
});
