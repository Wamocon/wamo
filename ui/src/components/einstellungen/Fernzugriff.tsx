"use client";

import { memo, useCallback, useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent, type SyntheticEvent } from "react";
import {
  CODE_HILFE,
  eingabePruefen,
  EINLEITUNG,
  hinweisZeilen,
  modusNachFehler,
  restSekunden,
  restzeitAnsage,
  restzeitKnapp,
  restzeitSprache,
  restzeitText,
  sortiereZiele,
  startFokus,
  startMeldung,
  STATUS_INFO,
  verbindenKnopf,
  zielZeile,
  type DialogModus,
  type Start,
  type Uebersicht,
  type Ziel,
} from "@/lib/fernzugriff";
import { beenden, starten, zieleLaden } from "@/lib/fernzugriffApi";
import { codeBestaetigen } from "@/lib/geraeteVerwaltungApi";
import { KernFehler } from "@/lib/kernApi";
import { createStore, useSelect } from "@/lib/store";

/** So oft wird die Liste neu geladen, solange das Fenster sichtbar und kein Dialog offen ist (belegt und frei ändern sich von selbst). */
const AUFFRISCHEN_MS = 30_000;

type Meldung = { text: string; art: "ok" | "fehler" };

// Stand in einem Store außerhalb der Komponente (wie bei der Geräteverwaltung): Laden und Anzeigen bleiben getrennt, und eine spät
// eintreffende Antwort trägt nichts mehr ein, wenn die Karte inzwischen zu ist. Die Adresse der Gateway-Seite (mit dem Sitzungstoken
// des Gateways) liegt nur hier im Speicher, nie in localStorage, und verschwindet mit dem Ende der Sitzung.
type Stand = {
  u: Uebersicht | null;
  lage: "laedt" | "ok" | "fehler";
  ladefehler: string;
  offeneSeite: { sitzung: string; url: string } | null;
};
const LEER: Stand = { u: null, lage: "laedt", ladefehler: "", offeneSeite: null };
const stand = createStore<Stand>(LEER);
let runde = 0; // gegen Überholen: nur die Antwort der letzten Anfrage zählt
let karteOffen = false;

async function laden() {
  if (!karteOffen) return;
  const meine = ++runde;
  try {
    const neu = await zieleLaden();
    if (meine !== runde) return;
    stand.set((alt) => ({
      ...alt,
      u: neu,
      lage: "ok",
      ladefehler: "",
      // die gemerkte Adresse gilt nur, solange genau diese Sitzung noch läuft
      offeneSeite: alt.offeneSeite && neu.meine?.sitzung === alt.offeneSeite.sitzung ? alt.offeneSeite : null,
    }));
  } catch (e) {
    if (meine !== runde) return;
    const text = e instanceof KernFehler && e.status !== 0 ? e.message : "Der Kern ist nicht erreichbar.";
    stand.set((alt) => ({ ...alt, lage: "fehler", ladefehler: text }));
  }
}

function leeren() {
  runde++;
  stand.set(() => LEER);
}

// ---------- Neues Fenster ----------
// Der Browser erlaubt ein neues Fenster nur direkt nach einer Eingabe der Person. Der Weg bis zur Adresse (Code bei Supabase
// bestätigen, Start im Kern) dauert länger. Deshalb öffnet der Absenden-Knopf sofort ein leeres Fenster und schickt es später zur
// Adresse. Vor dem Wechsel zur Gateway-Seite wird die Verbindung zur Oberfläche gelöst (opener = null): Die Gateway-Seite hat
// eine eigene Herkunft und sieht den Speicher der Oberfläche nie.
function leeresFenster(): Window | null {
  try {
    const w = window.open("", "_blank");
    if (w) {
      w.document.title = "WAMO Fernzugriff";
      w.document.body.textContent = "Die Verbindung wird aufgebaut.";
    }
    return w;
  } catch {
    return null;
  }
}

function inFensterOeffnen(w: Window | null, url: string): boolean {
  if (!w || w.closed) return false;
  try {
    w.opener = null;
    w.location.replace(url);
    return true;
  } catch {
    return false;
  }
}

function fensterSchliessen(w: Window | null) {
  try {
    w?.close();
  } catch {
    // schon zu
  }
}

// ---------- Anzeige ----------
function StatusPlakette({ z }: { z: Ziel }) {
  const info = STATUS_INFO[z.status];
  return (
    <span className="fz-status" data-status={z.status} title={info.erklaerung}>
      <span className="fz-marke" data-form={info.form} aria-hidden="true" />
      {info.wort}
    </span>
  );
}

function ZielKarte({ z, onVerbinden }: { z: Ziel; onVerbinden: (z: Ziel, ausloeser: HTMLButtonElement) => void }) {
  const titelId = useId();
  const knopf = verbindenKnopf(z);
  return (
    <li>
      <article className="fz-karte" data-status={z.status} data-eigene={z.eigene || undefined} aria-labelledby={titelId}>
        <div className="fz-karte-kopf">
          <StatusPlakette z={z} />
          {z.eigene && <span className="fz-tag">Deine Sitzung</span>}
        </div>
        <h6 id={titelId} className="fz-name">
          {z.anzeige}
        </h6>
        <p className="fz-zeile">{zielZeile(z)}</p>
        <div className="fz-aktionen">
          <button
            type="button"
            className={knopf.aktiv ? "btn-primary" : "btn-secondary"}
            disabled={!knopf.aktiv}
            aria-label={`${knopf.text}: ${z.anzeige}`}
            title={knopf.grund ?? undefined}
            onClick={(e) => onVerbinden(z, e.currentTarget)}
          >
            {knopf.text}
          </button>
        </div>
      </article>
    </li>
  );
}

/** Die laufende Sitzung: Restzeit (läuft sekündlich), Fenster öffnen, Trennen. Eigene Komponente, damit nur sie jede Sekunde neu rendert. */
function Sitzungsleiste({
  meine,
  seite,
  maxMinuten,
  onTrennen,
  beschaeftigt,
  onAbgelaufen,
}: {
  meine: NonNullable<Uebersicht["meine"]>;
  seite: string | null;
  maxMinuten: number;
  onTrennen: () => void;
  beschaeftigt: boolean;
  onAbgelaufen: () => void;
}) {
  const [rest, setRest] = useState(() => restSekunden(meine.endet, Date.now()));
  const [ansage, setAnsage] = useState("");
  const vorher = useRef(rest);
  useEffect(() => {
    const takt = () => {
      const jetzt = restSekunden(meine.endet, Date.now());
      const meldung = restzeitAnsage(vorher.current, jetzt);
      if (meldung) setAnsage(meldung);
      if (jetzt <= 0 && vorher.current > 0) onAbgelaufen();
      vorher.current = jetzt;
      setRest(jetzt);
    };
    takt();
    const uhr = setInterval(takt, 1000);
    return () => clearInterval(uhr);
  }, [meine.endet, onAbgelaufen]);
  const knapp = restzeitKnapp(rest);
  return (
    <section className="fz-sitzung" aria-labelledby="fz-sitzung-titel" data-knapp={knapp || undefined}>
      <h5 id="fz-sitzung-titel" className="fz-gruppe-titel">
        Verbunden mit {meine.anzeige}
      </h5>
      <p className="fz-rest">
        <span className="fz-rest-label">Restzeit</span>{" "}
        <time className="fz-rest-wert num" aria-label={`Restzeit ${restzeitSprache(rest)}`}>
          {restzeitText(rest)}
        </time>
      </p>
      <p className="fz-klein">
        Die Sitzung endet nach {maxMinuten} Minuten insgesamt. Der Desktop läuft in einem eigenen Fenster.
      </p>
      <div className="fz-aktionen">
        {seite && (
          <a className="btn-secondary fz-link" href={seite} target="_blank" rel="noopener noreferrer">
            Fenster öffnen
          </a>
        )}
        <button type="button" className="btn-primary" disabled={beschaeftigt} aria-busy={beschaeftigt || undefined} onClick={onTrennen}>
          {beschaeftigt ? "Wird getrennt …" : "Trennen"}
        </button>
      </div>
      <p className="sr-only" role="status" aria-live="polite">
        {ansage}
      </p>
    </section>
  );
}

/**
 * Dialog zum Verbinden (natives dialog-Element: Fokusfalle, Escape und Hintergrund sperren übernimmt der Browser). Er zeigt den
 * Hinweis zum Datenschutz. "Verbinden" startet ohne Code: Der Code der Anmeldung genügt (KP-2). Nur wenn der Kern den Start mit
 * zweiter_faktor_zu_alt ablehnt (die Anmeldung ist älter als sein Höchstalter), erscheint das Codefeld, und der Code geht erst an
 * Supabase und dann in einen zweiten Start. Den Fokus bekommt nie "Verbinden" (lib/fernzugriff.ts startFokus): Auch wenn WAMO die
 * Karte mit fernzugriff_oeffnen zeigt, startet nur ein echter Klick der Person.
 */
function VerbindenDialog({
  ziel,
  maxMinuten,
  leerlaufMinuten,
  onFertig,
  onFehler,
  onAbbrechen,
}: {
  ziel: Ziel;
  maxMinuten: number;
  leerlaufMinuten: number;
  onFertig: (start: Start, geoeffnet: boolean) => void;
  onFehler: (text: string, neuLaden: boolean) => void;
  onAbbrechen: () => void;
}) {
  const id = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const abbrechenRef = useRef<HTMLButtonElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const fehlerRef = useRef<HTMLParagraphElement>(null);
  const laeuft = useRef(false);
  const [modus, setModus] = useState<DialogModus>("klick");
  const [code, setCode] = useState("");
  const [fehler, setFehler] = useState("");
  const [beschaeftigt, setBeschaeftigt] = useState(false);

  useEffect(() => {
    const d = dialog.current;
    if (d && !d.open) d.showModal();
    // Der Fokus geht auf "Abbrechen", nie auf "Verbinden" (startFokus): Der Start braucht einen echten Klick der Person.
    if (startFokus("klick") === "abbrechen") abbrechenRef.current?.focus();
    // einmal beim Öffnen: der Dialog wird je Ziel neu aufgebaut (key)
  }, []);
  useEffect(() => {
    if (modus === "code" && startFokus(modus) === "code") codeRef.current?.focus(); // das Codefeld ist jetzt da
  }, [modus]);

  const absenden = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (laeuft.current) return;
    let eingabe: string | null = null; // nur mit Codefeld, sonst startet "Verbinden" ohne Code
    if (modus === "code") {
      const pruefung = eingabePruefen(code);
      if (!pruefung.ok) {
        setFehler(pruefung.text);
        codeRef.current?.focus();
        return;
      }
      eingabe = pruefung.code;
    }
    laeuft.current = true;
    setBeschaeftigt(true);
    setFehler("");
    const fenster = leeresFenster(); // sofort, solange der Tastendruck oder Tipp zählt
    let verwendet = false;
    try {
      if (eingabe !== null) {
        const bestaetigt = await codeBestaetigen(eingabe);
        if (!bestaetigt.ok) {
          setFehler(bestaetigt.text);
          setCode("");
          codeRef.current?.focus();
          return;
        }
      }
      const antwort = await starten(ziel.name, window.location.origin);
      if (antwort.ok) {
        verwendet = inFensterOeffnen(fenster, antwort.start.url);
        onFertig(antwort.start, verwendet);
        return;
      }
      if (antwort.dialogOffen) {
        // der Kern verlangt den Code (die Anmeldung ist zu alt): jetzt, und nur jetzt, erscheint das Codefeld
        setModus(modusNachFehler(modus, antwort));
        setFehler(antwort.text);
        setCode("");
        codeRef.current?.focus();
        return;
      }
      onFehler(antwort.text, antwort.neuLaden);
    } finally {
      if (!verwendet) fensterSchliessen(fenster);
      laeuft.current = false;
      setBeschaeftigt(false);
    }
  };

  // Escape: Während eine Anfrage läuft, bleibt der Dialog offen. Sonst schließt der Browser ihn und meldet das über close.
  const beiAbbruch = (e: SyntheticEvent<HTMLDialogElement>) => {
    if (laeuft.current) e.preventDefault();
  };
  const beiSchliessen = () => {
    if (!laeuft.current) onAbbrechen();
  };
  // Die Einstellungsseite schließt sich bei Escape; im Dialog gilt Escape nur für den Dialog
  const beiTaste = (e: KeyboardEvent<HTMLDialogElement>) => {
    if (e.key === "Escape") e.stopPropagation();
  };

  const zeilen = hinweisZeilen(ziel.anzeige, maxMinuten, leerlaufMinuten);
  return (
    <dialog
      ref={dialog}
      className="fz-dialog"
      aria-labelledby={`${id}-titel`}
      aria-describedby={`${id}-hinweis`}
      onKeyDown={beiTaste}
      onCancel={beiAbbruch}
      onClose={beiSchliessen}
    >
      <form className="fz-dialog-form" onSubmit={(e) => void absenden(e)} noValidate>
        <h4 id={`${id}-titel`} className="fz-dialog-titel">
          Mit {ziel.anzeige} verbinden
        </h4>
        <div id={`${id}-hinweis`} className="fz-hinweis">
          {zeilen.map((z) => (
            <p key={z}>{z}</p>
          ))}
        </div>
        {modus === "code" && (
          <div className="fz-feld">
            <label htmlFor={`${id}-code`}>Code aus der Authenticator-App</label>
            <input
              id={`${id}-code`}
              ref={codeRef}
              className="fz-code"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={7}
              value={code}
              readOnly={beschaeftigt}
              aria-invalid={fehler ? true : undefined}
              aria-describedby={`${id}-codehilfe${fehler ? ` ${id}-fehler` : ""}`}
              onChange={(e) => setCode(e.target.value)}
            />
            <span id={`${id}-codehilfe`} className="fz-klein">
              {CODE_HILFE}
            </span>
          </div>
        )}
        <p id={`${id}-fehler`} ref={fehlerRef} className="fz-fehler" role="alert">
          {fehler}
        </p>
        <div className="fz-dialog-knoepfe">
          <button ref={abbrechenRef} type="button" className="btn-secondary" disabled={beschaeftigt} onClick={onAbbrechen}>
            Abbrechen
          </button>
          <button type="submit" className="btn-primary" disabled={beschaeftigt} aria-busy={beschaeftigt || undefined}>
            {beschaeftigt ? "Einen Moment …" : "Verbinden"}
          </button>
        </div>
      </form>
    </dialog>
  );
}

/**
 * Karte "Fernzugriff" (Einstellungen, nur Admin): die Ziele mit Zustand (frei, belegt, nicht erreichbar), Verbinden mit Hinweis (Code nur,
 * wenn der Kern ihn verlangt), die laufende Sitzung mit Restzeit und Trennen. Der Desktop erscheint in einem eigenen Fenster der
 * Gateway-Adresse (eigene Herkunft, kein iframe). Maßgeblich bleibt der Kern: Rolle admin, Code der Anmeldung, ein Zuschauer je Ziel.
 * Das Zeigen der Karte (auch auf Bitte von WAMO) startet nie eine Sitzung und öffnet keinen Dialog.
 */
export const Fernzugriff = memo(function Fernzugriff() {
  const u = useSelect(stand, (s) => s.u);
  const lage = useSelect(stand, (s) => s.lage);
  const ladefehler = useSelect(stand, (s) => s.ladefehler);
  const seite = useSelect(stand, (s) => s.offeneSeite);
  const [meldung, setMeldung] = useState<Meldung | null>(null);
  const [dialog, setDialog] = useState<Ziel | null>(null);
  const [trennt, setTrennt] = useState(false);
  const meldungRef = useRef<HTMLParagraphElement>(null);
  const ausloeser = useRef<HTMLElement | null>(null);
  const dialogOffen = useRef(false);

  useEffect(() => {
    karteOffen = true;
    void laden();
    const uhr = setInterval(() => {
      if (document.visibilityState === "visible" && !dialogOffen.current) void laden();
    }, AUFFRISCHEN_MS);
    return () => {
      clearInterval(uhr);
      karteOffen = false;
      leeren();
    };
  }, []);

  const oeffnen = (z: Ziel, knopf: HTMLButtonElement) => {
    ausloeser.current = knopf;
    dialogOffen.current = true;
    setMeldung(null);
    setDialog(z);
  };
  const schliessen = () => {
    dialogOffen.current = false;
    setDialog(null);
  };
  const abbrechen = () => {
    schliessen();
    const knopf = ausloeser.current;
    setTimeout(() => (knopf?.isConnected ? knopf.focus() : meldungRef.current?.focus()), 0);
  };
  const meldungZeigen = (m: Meldung) => {
    setMeldung(m);
    setTimeout(() => meldungRef.current?.focus(), 0);
  };
  const gestartet = (start: Start, geoeffnet: boolean) => {
    schliessen();
    stand.set((alt) => ({ ...alt, offeneSeite: { sitzung: start.sitzung, url: start.url } }));
    meldungZeigen({ text: startMeldung(start.anzeige, geoeffnet), art: "ok" });
    void laden();
  };
  const fehlgeschlagen = (text: string, neuLaden: boolean) => {
    schliessen();
    meldungZeigen({ text, art: "fehler" });
    if (neuLaden) void laden();
  };
  const trennen = async () => {
    const meine = stand.get().u?.meine;
    if (!meine || trennt) return;
    setTrennt(true);
    try {
      const r = await beenden(meine.sitzung, meine.anzeige);
      if (r.ok) stand.set((alt) => ({ ...alt, offeneSeite: null }));
      meldungZeigen({ text: r.text, art: r.ok ? "ok" : "fehler" });
      if (r.ok || r.neuLaden) await laden();
    } finally {
      setTrennt(false);
    }
  };
  const abgelaufen = useCallback(() => {
    setMeldung({ text: "Die Fernsitzung ist zu Ende.", art: "ok" });
    setTimeout(() => meldungRef.current?.focus(), 0);
    stand.set((alt) => ({ ...alt, offeneSeite: null }));
    void laden();
  }, []);

  const ziele = u ? sortiereZiele(u.ziele) : [];
  return (
    <div className="fz" role="region" aria-label="Fernzugriff">
      <p className="ez-text">{EINLEITUNG}</p>
      <div className="fz-kopf">
        <p ref={meldungRef} className="fz-meldung" data-art={meldung?.art} role="status" tabIndex={-1}>
          {meldung?.text ?? ""}
        </p>
        <button type="button" className="btn-secondary btn-small" onClick={() => void laden()}>
          Liste neu laden
        </button>
      </div>

      {lage === "laedt" && !u && (
        <p className="ez-leer" role="status">
          Die Ziele werden geladen …
        </p>
      )}
      {lage === "fehler" && (
        <p className="fz-meldung" data-art="fehler" role="alert">
          {u ? "Die Liste konnte nicht aktualisiert werden, sie zeigt den Stand von vorher. " : ""}
          {ladefehler}
        </p>
      )}
      {u && !u.aktiv && (
        <p className="ez-leer" role="status">
          Der Fernzugriff ist auf diesem Kern noch nicht eingeschaltet.
        </p>
      )}
      {u?.aktiv && u.meine && (
        <Sitzungsleiste
          meine={u.meine}
          seite={seite?.sitzung === u.meine.sitzung ? seite.url : null}
          maxMinuten={u.maxMinuten}
          onTrennen={() => void trennen()}
          beschaeftigt={trennt}
          onAbgelaufen={abgelaufen}
        />
      )}
      {u?.aktiv && u.ziele.length === 0 && lage !== "fehler" && (
        <p className="ez-leer" role="status">
          Es sind noch keine Ziele eingetragen.
        </p>
      )}
      {u?.aktiv && ziele.length > 0 && (
        <section className="fz-gruppe" aria-label="Ziele">
          <h5 className="fz-gruppe-titel">Ziele ({ziele.length})</h5>
          <ul className="fz-liste">
            {ziele.map((z) => (
              <ZielKarte key={z.name} z={z} onVerbinden={oeffnen} />
            ))}
          </ul>
        </section>
      )}

      {dialog && u && (
        <VerbindenDialog
          key={dialog.name}
          ziel={dialog}
          maxMinuten={u.maxMinuten}
          leerlaufMinuten={u.leerlaufMinuten}
          onFertig={gestartet}
          onFehler={fehlgeschlagen}
          onAbbrechen={abbrechen}
        />
      )}
    </div>
  );
});
