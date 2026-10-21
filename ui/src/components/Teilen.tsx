"use client";

import { useEffect, useId, useState } from "react";
import { KernFehler, kernJson, type Profil, type Recht } from "@/lib/kernApi";

/** Was geteilt wird: ein eigenes Canvas-Element, ein Projekt (Ergebnisse eines Entwicklungsauftrags) oder eine Datei. */
export type TeilZiel = { art: "canvas" | "datei"; id: string; titel: string; projekt?: string };

type Antwort = { id: string; neu: boolean };

/** Aktive Profile außer dem eigenen (bei jedem Öffnen frisch: kein Stand eines früher angemeldeten Profils). */
async function ladeProfile(): Promise<Profil[]> {
  return (await kernJson<{ profile: Profil[] }>("/profile")).profile;
}

/**
 * Knopf "Teilen" mit einem kleinen Formular darunter: Empfänger, Recht, Nachricht. Geteilt wird nur per Klick des
 * Besitzers (nie per Sprache); der Kern prüft Besitz, Empfänger und Recht noch einmal, die Datenbank ebenso.
 * Projekte (Entwicklungsaufträge) werden nur zum Lesen geteilt.
 */
export function TeilenKnopf({ ziel }: { ziel: TeilZiel }) {
  const [offen, setOffen] = useState(false);
  return (
    <div className="teilen">
      <button
        type="button"
        className="btn-secondary btn-small"
        aria-expanded={offen}
        onClick={() => setOffen((o) => !o)}
      >
        {offen ? "Teilen abbrechen" : "Teilen"}
      </button>
      {offen && <TeilenFormular ziel={ziel} onFertig={() => setOffen(false)} />}
    </div>
  );
}

function TeilenFormular({ ziel, onFertig }: { ziel: TeilZiel; onFertig: () => void }) {
  const id = useId();
  const [profile, setProfile] = useState<Profil[] | null>(null);
  const [empfaenger, setEmpfaenger] = useState("");
  const [recht, setRecht] = useState<Recht>("lesen");
  const [projekt, setProjekt] = useState(false);
  const [nachricht, setNachricht] = useState("");
  const [status, setStatus] = useState<{ text: string; fehler: boolean } | null>(null);
  const [laeuft, setLaeuft] = useState(false);

  useEffect(() => {
    let aktiv = true;
    ladeProfile().then(
      (p) => aktiv && setProfile(p),
      (e: unknown) => aktiv && setStatus({ text: e instanceof KernFehler ? e.message : "Profile nicht ladbar", fehler: true }),
    );
    return () => {
      aktiv = false;
    };
  }, []);

  const teilen = async () => {
    if (!empfaenger || laeuft) return;
    setLaeuft(true);
    setStatus(null);
    const body =
      projekt && ziel.projekt
        ? { art: "auftrag", objekt: ziel.projekt, empfaenger, recht: "lesen", hinweis: nachricht || undefined }
        : { art: ziel.art, objekt: ziel.id, empfaenger, recht, hinweis: nachricht || undefined };
    try {
      const antwort = await kernJson<Antwort>("/teilen", "POST", body);
      const name = profile?.find((p) => p.user_id === empfaenger)?.name ?? "die Person";
      setStatus({ text: antwort.neu ? `Geteilt mit ${name}.` : `Recht für ${name} angepasst.`, fehler: false });
      setNachricht("");
    } catch (e) {
      setStatus({ text: e instanceof KernFehler ? e.message : "Teilen fehlgeschlagen", fehler: true });
    } finally {
      setLaeuft(false);
    }
  };

  return (
    <form
      className="teilen-form"
      aria-label={`„${ziel.titel}“ teilen`}
      onSubmit={(e) => {
        e.preventDefault();
        void teilen();
      }}
    >
      <label htmlFor={`${id}-mit`}>Teilen mit</label>
      <select
        id={`${id}-mit`}
        value={empfaenger}
        onChange={(e) => setEmpfaenger(e.target.value)}
        disabled={!profile || laeuft}
        required
      >
        <option value="">{profile ? "Person wählen" : "lädt …"}</option>
        {profile?.map((p) => (
          <option key={p.user_id} value={p.user_id}>
            {p.name}
          </option>
        ))}
      </select>
      {ziel.projekt && (
        <label className="teilen-check">
          <input type="checkbox" checked={projekt} onChange={(e) => setProjekt(e.target.checked)} />
          <span>Ganzes Projekt {ziel.projekt} teilen (alle Ergebnisse, nur lesen)</span>
        </label>
      )}
      <fieldset className="teilen-recht" disabled={projekt && Boolean(ziel.projekt)}>
        <legend>Recht</legend>
        {(["lesen", "bearbeiten"] as const).map((r) => (
          <label key={r}>
            <input type="radio" name={`${id}-recht`} value={r} checked={recht === r} onChange={() => setRecht(r)} />
            <span>{r === "lesen" ? "Lesen" : "Lesen und bearbeiten"}</span>
          </label>
        ))}
      </fieldset>
      <label htmlFor={`${id}-nachricht`}>Nachricht (optional)</label>
      <input
        id={`${id}-nachricht`}
        type="text"
        maxLength={300}
        value={nachricht}
        onChange={(e) => setNachricht(e.target.value)}
        disabled={laeuft}
      />
      <div className="teilen-aktionen">
        <button type="submit" className="btn-secondary btn-small" disabled={!empfaenger || laeuft}>
          {laeuft ? "Teile …" : "Jetzt teilen"}
        </button>
        <button type="button" className="error-close" onClick={onFertig}>
          Schließen
        </button>
      </div>
      {status && (
        <p className="teilen-status" role="status" data-fehler={status.fehler || undefined}>
          {status.text}
        </p>
      )}
    </form>
  );
}
