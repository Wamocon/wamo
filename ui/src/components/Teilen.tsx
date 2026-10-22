"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { alsProfile, KernFehler, kernJson, type Profil, type Recht } from "@/lib/kernApi";

/** Was geteilt wird: ein eigenes Canvas-Element (optional mit Projekt eines Auftrags) oder eine Datei. */
export type TeilZiel = { art: "canvas" | "datei"; id: string; titel: string; projekt?: string; nurLesen?: boolean };

/**
 * Knopf "Teilen" mit einem kleinen Formular darunter: Empfänger, Recht, Nachricht. Geteilt wird nur per Klick des
 * Besitzers (nie per Sprache); der Kern prüft Besitz, Empfänger und Recht noch einmal, die Datenbank ebenso.
 * Projekte und Ergebnisse von Entwicklungsaufträgen werden nur zum Lesen geteilt. Beim Schließen kehrt der Fokus auf
 * den Knopf zurück.
 */
export function TeilenKnopf({ ziel }: { ziel: TeilZiel }) {
  const [offen, setOffen] = useState(false);
  const knopf = useRef<HTMLButtonElement>(null);
  const schliessen = useCallback(() => {
    setOffen(false);
    requestAnimationFrame(() => knopf.current?.focus());
  }, []);
  return (
    <div className="teilen">
      <button
        ref={knopf}
        type="button"
        className="btn-secondary btn-small"
        aria-expanded={offen}
        aria-label={`„${ziel.titel}“ teilen`}
        onClick={() => (offen ? schliessen() : setOffen(true))}
      >
        {offen ? "Teilen abbrechen" : "Teilen"}
      </button>
      {offen && <TeilenFormular ziel={ziel} onFertig={schliessen} />}
    </div>
  );
}

type Status = { text: string; fehler: boolean } | null;

function TeilenFormular({ ziel, onFertig }: { ziel: TeilZiel; onFertig: () => void }) {
  const id = useId();
  const [profile, setProfile] = useState<Profil[] | null>(null);
  const [ladefehler, setLadefehler] = useState<string | null>(null);
  const [empfaenger, setEmpfaenger] = useState("");
  const [recht, setRecht] = useState<Recht>("lesen");
  const [projekt, setProjekt] = useState(false);
  const [nachricht, setNachricht] = useState("");
  const [status, setStatus] = useState<Status>(null);
  const [laeuft, setLaeuft] = useState(false);
  const auswahl = useRef<HTMLSelectElement>(null);
  const nurLesen = Boolean(ziel.nurLesen) || (projekt && Boolean(ziel.projekt));

  // Profile bei jedem Öffnen frisch (kein Stand eines früher angemeldeten Profils); erneut über "Erneut laden"
  const [ladeRunde, setLadeRunde] = useState(0);
  useEffect(() => {
    let aktiv = true;
    kernJson("/profile").then(
      (d) => {
        if (!aktiv) return;
        setProfile(alsProfile((d as { profile?: unknown })?.profile));
        setLadefehler(null);
        requestAnimationFrame(() => auswahl.current?.focus());
      },
      (e: unknown) => aktiv && setLadefehler(e instanceof KernFehler ? e.message : "Profile nicht ladbar"),
    );
    return () => {
      aktiv = false;
    };
  }, [ladeRunde]);

  const teilen = async () => {
    if (!empfaenger || laeuft) return;
    setLaeuft(true);
    setStatus(null);
    const body =
      projekt && ziel.projekt
        ? { art: "auftrag", objekt: ziel.projekt, empfaenger, recht: "lesen", hinweis: nachricht || undefined }
        : { art: ziel.art, objekt: ziel.id, empfaenger, recht: nurLesen ? "lesen" : recht, hinweis: nachricht || undefined };
    try {
      const antwort = (await kernJson("/teilen", "POST", body)) as { neu?: unknown };
      const name = profile?.find((p) => p.user_id === empfaenger)?.name ?? "die Person";
      setStatus({ text: antwort?.neu === false ? `Recht für ${name} angepasst.` : `Geteilt mit ${name}.`, fehler: false });
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
      aria-busy={laeuft || (!profile && !ladefehler)}
      onSubmit={(e) => {
        e.preventDefault();
        void teilen();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onFertig();
        }
      }}
    >
      <label htmlFor={`${id}-mit`}>Teilen mit</label>
      {ladefehler ? (
        <div className="teilen-fehler" role="alert">
          <span>{ladefehler}</span>
          <button type="button" className="error-close" onClick={() => setLadeRunde((r) => r + 1)}>
            Erneut laden
          </button>
        </div>
      ) : (
        <select
          id={`${id}-mit`}
          ref={auswahl}
          value={empfaenger}
          onChange={(e) => setEmpfaenger(e.target.value)}
          disabled={!profile || laeuft}
          required
        >
          <option value="">{profile ? (profile.length ? "Person wählen" : "keine weiteren Profile") : "lädt …"}</option>
          {profile?.map((p) => (
            <option key={p.user_id} value={p.user_id}>
              {p.name}
            </option>
          ))}
        </select>
      )}
      {ziel.projekt && (
        <label className="teilen-check">
          <input type="checkbox" checked={projekt} onChange={(e) => setProjekt(e.target.checked)} />
          <span>Ganzes Projekt {ziel.projekt} teilen (alle Ergebnisse)</span>
        </label>
      )}
      <fieldset className="teilen-recht" disabled={nurLesen}>
        <legend>Recht</legend>
        {(["lesen", "bearbeiten"] as const).map((r) => (
          <label key={r}>
            <input
              type="radio"
              name={`${id}-recht`}
              value={r}
              checked={(nurLesen ? "lesen" : recht) === r}
              onChange={() => setRecht(r)}
            />
            <span>{r === "lesen" ? "Lesen" : "Lesen und bearbeiten"}</span>
          </label>
        ))}
      </fieldset>
      {nurLesen && <p className="teilen-hinweis">Ergebnisse von Entwicklungsaufträgen werden nur zum Lesen geteilt.</p>}
      <label htmlFor={`${id}-nachricht`}>Nachricht (optional)</label>
      <input
        id={`${id}-nachricht`}
        type="text"
        maxLength={300}
        value={nachricht}
        onChange={(e) => setNachricht(e.target.value)}
        disabled={laeuft}
      />
      <p className="teilen-hinweis">
        Die Person sieht Titel und Inhalt. Fragt sie WAMO danach, gehen Auszüge in ihrem Gespräch an das Sprachmodell.
      </p>
      <div className="teilen-aktionen">
        <button type="submit" className="btn-secondary btn-small" disabled={!empfaenger || laeuft || !profile}>
          {laeuft ? "Teile …" : "Jetzt teilen"}
        </button>
        <button type="button" className="error-close" onClick={onFertig}>
          Schließen
        </button>
      </div>
      {status && (
        <p className="teilen-status" role={status.fehler ? "alert" : "status"} data-fehler={status.fehler || undefined}>
          {status.text}
        </p>
      )}
    </form>
  );
}
