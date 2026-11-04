"use client";

import { memo, useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent, type SyntheticEvent } from "react";
import type { Anfrage } from "@/lib/aenderungen";
import { freigebenSenden } from "@/lib/aenderungenApi";
import { codeFrisch, erfolgText, fehlerFuer, freigabeBody, voraussetzung } from "@/lib/aenderungenFreigabe";
import { aal2Zeitpunkt } from "@/lib/anmeldung";
import { codeBestaetigen } from "@/lib/geraeteVerwaltungApi";
import { accessToken } from "@/lib/supabase";

/**
 * Freigabe einer Änderung (Schritt 1.5b): Knopf in der Karte "Änderungen" und ein Dialog mit Code der Authenticator-App. Gesendet wird an den
 * Freigabe-Dienst des Ankers (nicht an den Kern); er bindet die Freigabe an Commit und Baum, die diese Karte zeigt, und entscheidet allein. Die
 * Logik (Voraussetzungen, Fehlertexte, Frische des Codes) steht in lib/aenderungenFreigabe.ts.
 */
export type Ausgang = { text: string; art: "ok" | "fehler"; neuLaden: boolean };

export const AenderungenFreigabe = memo(function AenderungenFreigabe({ anfrage, onAusgang }: { anfrage: Anfrage; onAusgang: (a: Ausgang) => void }) {
  const [offen, setOffen] = useState(false);
  const v = voraussetzung(anfrage);
  const knopfRef = useRef<HTMLButtonElement>(null);
  if (!v.moeglich) {
    return v.text ? (
      <p className="ez-klein" role="status">
        {v.text}
      </p>
    ) : null;
  }
  return (
    <>
      {v.text && <p className="ez-klein">{v.text}</p>}
      <div className="ae-aktionen">
        <button ref={knopfRef} type="button" className="btn-primary" onClick={() => setOffen(true)}>
          Änderung freigeben …
        </button>
      </div>
      {offen && (
        <FreigabeDialog
          anfrage={anfrage}
          pflichtHaken={v.pflichtHaken}
          onZu={() => {
            setOffen(false);
            requestAnimationFrame(() => knopfRef.current?.focus());
          }}
          onAusgang={(a) => {
            setOffen(false);
            onAusgang(a);
          }}
        />
      )}
    </>
  );
});

function FreigabeDialog({ anfrage, pflichtHaken, onZu, onAusgang }: { anfrage: Anfrage; pflichtHaken: boolean; onZu: () => void; onAusgang: (a: Ausgang) => void }) {
  const id = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const fehlerRef = useRef<HTMLParagraphElement>(null);
  const laeuft = useRef(false);
  const [code, setCode] = useState("");
  const [haken, setHaken] = useState(false);
  const [fehler, setFehler] = useState("");
  const [beschaeftigt, setBeschaeftigt] = useState(false);
  const dateien = anfrage.pruefung?.diff?.anzahl ?? null;

  useEffect(() => {
    const d = dialog.current;
    if (d && !d.open) d.showModal();
    codeRef.current?.focus();
  }, []);

  const zeigeFehler = (text: string, codeLeeren = false) => {
    setFehler(text);
    if (codeLeeren) setCode("");
    requestAnimationFrame(() => (codeLeeren ? codeRef.current : fehlerRef.current)?.focus());
  };

  const absenden = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (laeuft.current) return; // kein Doppelklick: eine Freigabe wird nie zweimal gesendet
    const sauber = code.replace(/\s+/g, "");
    if (!/^\d{6}$/.test(sauber)) return zeigeFehler("Bitte gib die sechs Ziffern aus der Authenticator-App ein.");
    if (pflichtHaken && !haken) return zeigeFehler("Bitte bestätige, dass die Änderung die Schutzschicht berührt und du sie geprüft hast.");
    const body = freigabeBody(anfrage);
    if (!body) return zeigeFehler("Commit und Baum fehlen. Bitte lade die Karte neu.");
    laeuft.current = true;
    setBeschaeftigt(true);
    setFehler("");
    try {
      const bestaetigt = await codeBestaetigen(sauber);
      if (!bestaetigt.ok) return zeigeFehler(bestaetigt.text, true);
      const token = await accessToken();
      // Der Freigabe-Dienst nimmt nur Codes bis 60 Sekunden, die Oberfläche sonst bis 120: vor dem Senden zählt die strengere Grenze
      if (!token || !codeFrisch(aal2Zeitpunkt(token), Date.now())) {
        return zeigeFehler("Der Code ist nicht mehr frisch genug. Bitte gib gleich noch einen neuen Code ein.", true);
      }
      const r = await freigebenSenden(anfrage.id, body.commit_id, body.tree_id, token);
      if (r.ok) return onAusgang({ text: erfolgText(r.giltS), art: "ok", neuLaden: true });
      const f = fehlerFuer(r.code, r.status);
      if (f.erfolg) return onAusgang({ text: f.text, art: "ok", neuLaden: f.neuLaden });
      if (f.offen) return zeigeFehler(f.text, f.neuerCode === true);
      return onAusgang({ text: f.text, art: "fehler", neuLaden: f.neuLaden });
    } finally {
      laeuft.current = false;
      setBeschaeftigt(false);
    }
  };

  const beiAbbruch = (e: SyntheticEvent<HTMLDialogElement>) => {
    if (laeuft.current) e.preventDefault(); // eine laufende Freigabe lässt sich nicht abbrechen
  };
  const beiSchliessen = () => {
    if (!laeuft.current) onZu();
  };
  const beiTaste = (e: KeyboardEvent<HTMLDialogElement>) => {
    if (e.key === "Escape") e.stopPropagation(); // die Einstellungsseite schließt sich bei Escape, hier gilt es nur für den Dialog
  };

  return (
    <dialog ref={dialog} className="gv-dialog" aria-labelledby={`${id}-titel`} aria-describedby={`${id}-text`} onKeyDown={beiTaste} onCancel={beiAbbruch} onClose={beiSchliessen}>
      <form className="gv-dialog-form" onSubmit={(e) => void absenden(e)} noValidate>
        <h4 id={`${id}-titel`} className="gv-dialog-titel">
          Änderung freigeben
        </h4>
        <p id={`${id}-text`} className="gv-text">
          Du gibst genau diese Fassung frei. Danach wird sie bereitgestellt und umgeschaltet; der Kern läuft nach der Umschaltung 30 Minuten auf Bewährung.
        </p>
        <dl className="ae-fassung">
          <div>
            <dt>Version</dt>
            <dd>{anfrage.version ?? "unbekannt"}</dd>
          </div>
          <div>
            <dt>Commit</dt>
            <dd className="num">{anfrage.v_commit?.slice(0, 12)}</dd>
          </div>
          <div>
            <dt>Baum</dt>
            <dd className="num">{anfrage.tree_id?.slice(0, 12)}</dd>
          </div>
          {dateien !== null && (
            <div>
              <dt>Dateien</dt>
              <dd>{dateien}</dd>
            </div>
          )}
          {anfrage.treffer_anzahl !== null && (
            <div>
              <dt>Funde der Prüfung</dt>
              <dd>{anfrage.treffer_anzahl}</dd>
            </div>
          )}
        </dl>
        {pflichtHaken && (
          <label className="gv-check">
            <input type="checkbox" checked={haken} disabled={beschaeftigt} onChange={(e) => setHaken(e.target.checked)} />
            <span>Ich weiß, dass diese Änderung die Schutzschicht berührt, und habe sie geprüft.</span>
          </label>
        )}
        <div className="gv-feld">
          <label htmlFor={`${id}-code`}>Code aus der Authenticator-App</label>
          <input
            id={`${id}-code`}
            ref={codeRef}
            className="gv-code"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={7}
            value={code}
            readOnly={beschaeftigt}
            aria-describedby={`${id}-codehilfe${fehler ? ` ${id}-fehler` : ""}`}
            aria-invalid={fehler ? true : undefined}
            onChange={(e) => setCode(e.target.value)}
          />
          <span id={`${id}-codehilfe`} className="gv-zaehler">
            Der Code muss ganz frisch sein (höchstens 45 Sekunden alt, wenn du abschickst).
          </span>
        </div>
        <p id={`${id}-fehler`} ref={fehlerRef} className="gv-fehler" role="alert" tabIndex={-1}>
          {fehler}
        </p>
        <div className="gv-dialog-knoepfe">
          <button type="button" className="btn-secondary" disabled={beschaeftigt} onClick={onZu}>
            Abbrechen
          </button>
          <button type="submit" className="btn-primary" disabled={beschaeftigt} aria-busy={beschaeftigt || undefined}>
            {beschaeftigt ? "Einen Moment …" : "Freigeben"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
