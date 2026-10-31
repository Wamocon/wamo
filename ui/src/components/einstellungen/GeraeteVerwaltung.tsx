"use client";

import { memo, useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { GERAETEVERWALTUNG_ID } from "@/lib/einstellungenAbschnitte";
import {
  aktionenFuer,
  aktionKnopf,
  bestaetigungFuer,
  eingabePruefen,
  ereignisText,
  erfolgText,
  fingerabdruckGruppen,
  GRUND_MAX,
  istDieserLaptop,
  sortiere,
  STATUS_INFO,
  wartende,
  zeitText,
  type Aktion,
  type Bestaetigung,
  type Eingabe,
  type Geraet,
} from "@/lib/geraeteVerwaltung";
import { aktionSenden, codeBestaetigen, geraeteLaden } from "@/lib/geraeteVerwaltungApi";
import { geraeteAnzeige } from "@/lib/geraeteAnzeige";
import { KernFehler } from "@/lib/kernApi";
import { createStore, useSelect } from "@/lib/store";

/** So oft wird die Liste neu geladen, solange das Fenster sichtbar und kein Dialog offen ist (neue Anträge erscheinen von selbst). */
const AUFFRISCHEN_MS = 60_000;

type Meldung = { text: string; art: "ok" | "fehler" };
type Dialog = { geraet: Geraet; aktion: Aktion; hier: boolean };

function Fingerabdruck({ fp }: { fp: string | null }) {
  const g = fingerabdruckGruppen(fp);
  if (!g) return <span>unbekannt</span>;
  // Als Text in Gruppen; Screenreader lesen die Gruppen nacheinander, die Leerzeichen trennen sie
  return (
    <p className="gv-fp">
      <span className="gv-fp-praefix">{g.praefix}</span>
      {g.gruppen.map((x, i) => (
        <span key={i} className="gv-fp-gruppe">
          {x}
        </span>
      ))}
    </p>
  );
}

function StatusPlakette({ status }: { status: Geraet["status"] }) {
  const info = STATUS_INFO[status];
  return (
    <span className="gv-status" data-status={status} title={info.erklaerung}>
      <span className="gv-marke" data-form={info.form} aria-hidden="true" />
      {info.wort}
    </span>
  );
}

function GeraeteKarte({
  g,
  hier,
  onAktion,
}: {
  g: Geraet;
  hier: boolean;
  onAktion: (g: Geraet, aktion: Aktion, ausloeser: HTMLButtonElement) => void;
}) {
  const titelId = useId();
  const aktionen = aktionenFuer(g);
  const zeilen: { name: string; wert: string }[] = [
    { name: "Profil", wert: g.profilName ? `${g.profilName}${g.profilAktiv ? "" : " (deaktiviert)"}` : "kein Besitzer" },
    { name: "Rechnername", wert: g.rechnername ?? "unbekannt" },
    { name: "Beantragt am", wert: zeitText(g.erstellt) },
  ];
  if (g.freigegeben && g.status !== "beantragt") zeilen.push({ name: "Freigegeben am", wert: zeitText(g.freigegeben) });
  if (g.gesperrt) zeilen.push({ name: g.status === "widerrufen" ? "Widerrufen am" : "Gesperrt am", wert: zeitText(g.gesperrt) });
  if (g.grund) zeilen.push({ name: "Grund", wert: g.grund });
  if (g.status === "aktiv" || g.status === "gesperrt") zeilen.push({ name: "Zuletzt gesehen", wert: g.zuletztGesehen ? zeitText(g.zuletztGesehen) : "noch nie" });
  return (
    <li>
      <article className="gv-karte" data-status={g.status} aria-labelledby={titelId}>
        <div className="gv-karte-kopf">
          <StatusPlakette status={g.status} />
          {hier && (
            <span className="gv-tag" data-art="hier">
              Dieses Gerät
            </span>
          )}
          {g.eigen && !hier && <span className="gv-tag">Dein Gerät</span>}
          {g.bindung === "automatisch" && <span className="gv-tag">Automatisch gebunden</span>}
        </div>
        <div>
          <h5 id={titelId} className="gv-name">
            {g.name}
          </h5>
          <span className="gv-kennung">{g.kennung}</span>
        </div>
        <dl className="gv-daten">
          {zeilen.map((z) => (
            <div key={z.name} className="gv-zeile">
              <dt>{z.name}</dt>
              <dd>{z.wert}</dd>
            </div>
          ))}
          {g.status !== "widerrufen" && (
            <div className="gv-zeile">
              <dt>Fingerabdruck</dt>
              <dd>
                <Fingerabdruck fp={g.fingerabdruck} />
              </dd>
            </div>
          )}
        </dl>
        {aktionen.length > 0 && (
          <div className="gv-aktionen">
            {aktionen.map((a) => (
              <button
                key={a}
                type="button"
                className={a === "freigeben" ? "btn-primary btn-small" : "btn-secondary btn-small"}
                aria-label={`${aktionKnopf(g, a)}: ${g.name} (${g.kennung})`}
                onClick={(e) => onAktion(g, a, e.currentTarget)}
              >
                {aktionKnopf(g, a)}
              </button>
            ))}
          </div>
        )}
        {g.ereignisse.length > 0 && (
          <details className="gv-verlauf">
            <summary>Verlauf ({g.ereignisse.length})</summary>
            <ul>
              {g.ereignisse.map((e, i) => (
                <li key={i}>{ereignisText(e)}</li>
              ))}
            </ul>
          </details>
        )}
      </article>
    </li>
  );
}

/**
 * Bestätigungsdialog (natives dialog-Element: Fokusfalle, Escape und Hintergrund sperren übernimmt der Browser).
 * Ein Gerät wird nie mit einem einzigen Klick entschieden: Der Dialog zeigt den Fingerabdruck erneut, verlangt bei
 * Freigabe und Entsperren das ausdrückliche Bestätigen des Vergleichs am Laptop, bei Sperre und Widerruf einen Grund
 * und bei jeder Aktion einen frischen Code des zweiten Faktors. Die Prüfregeln stehen in lib/geraeteVerwaltung.ts.
 */
function BestaetigungsDialog({
  daten,
  onFertig,
  onAbbrechen,
}: {
  daten: Dialog;
  onFertig: (meldung: Meldung, neuLaden: boolean) => void;
  onAbbrechen: () => void;
}) {
  const { geraet, aktion, hier } = daten;
  const b: Bestaetigung = bestaetigungFuer(geraet, aktion, { dieserLaptop: hier });
  const id = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const verglichenRef = useRef<HTMLInputElement>(null);
  const grundRef = useRef<HTMLTextAreaElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);
  const fehlerRef = useRef<HTMLParagraphElement>(null);
  const laeuft = useRef(false);
  const [eingabe, setEingabe] = useState<Eingabe>({ grund: "", verglichen: false, code: "" });
  const [fehler, setFehler] = useState<{ text: string; feld: "verglichen" | "grund" | "code" | null } | null>(null);
  const [beschaeftigt, setBeschaeftigt] = useState(false);

  const fokus = (feld: "verglichen" | "grund" | "code" | null) => {
    const ziel = feld === "verglichen" ? verglichenRef.current : feld === "grund" ? grundRef.current : feld === "code" ? codeRef.current : fehlerRef.current;
    ziel?.focus();
  };
  const ersterAutofokus = b.vergleichNoetig ? "verglichen" : b.grundNoetig ? "grund" : "code";

  // Öffnen als modales Fenster; der Fokus beginnt beim ersten Feld, das etwas verlangt
  useEffect(() => {
    const d = dialog.current;
    if (d && !d.open) d.showModal();
    fokus(ersterAutofokus);
    return () => {
      if (d?.open) d.close();
    };
    // einmal beim Öffnen: der Dialog wird je Gerät und Aktion neu aufgebaut (key)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const absenden = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (laeuft.current) return;
    const pruefung = eingabePruefen(b, eingabe);
    if (!pruefung.ok) {
      setFehler({ text: pruefung.text, feld: pruefung.feld });
      fokus(pruefung.feld);
      return;
    }
    laeuft.current = true;
    setBeschaeftigt(true);
    setFehler(null);
    try {
      const code = await codeBestaetigen(pruefung.code);
      if (!code.ok) {
        setFehler({ text: code.text, feld: "code" });
        setEingabe((alt) => ({ ...alt, code: "" }));
        fokus("code");
        return;
      }
      const antwort = await aktionSenden({
        kennung: geraet.kennung,
        aktion,
        grund: pruefung.grund,
        // der Fingerabdruck, den der Dialog gezeigt hat; der Kern vergleicht ihn mit dem Gerät
        fingerabdruck: b.vergleichNoetig ? b.fingerabdruck : null,
      });
      if (antwort.ok) {
        onFertig({ text: erfolgText(geraet, aktion, antwort.ergebnis), art: "ok" }, true);
        return;
      }
      if (antwort.dialogOffen) {
        setFehler({ text: antwort.text, feld: antwort.feld });
        if (antwort.feld === "code") setEingabe((alt) => ({ ...alt, code: "" }));
        fokus(antwort.feld);
        return;
      }
      onFertig({ text: antwort.text, art: "fehler" }, antwort.neuLaden);
    } finally {
      laeuft.current = false;
      setBeschaeftigt(false);
    }
  };

  // Escape: der Browser schließt das Dialogfenster selbst; währenddessen läuft eine Anfrage, die nicht abbrechbar ist
  const beiAbbruch = (e: Event) => {
    e.preventDefault();
    if (!laeuft.current) onAbbrechen();
  };
  useEffect(() => {
    const d = dialog.current;
    d?.addEventListener("cancel", beiAbbruch);
    return () => d?.removeEventListener("cancel", beiAbbruch);
  });
  // Die Einstellungsseite schließt sich bei Escape; im Dialog gilt Escape nur für den Dialog
  const beiTaste = (e: KeyboardEvent<HTMLDialogElement>) => {
    if (e.key === "Escape") e.stopPropagation();
  };

  const fehlerFeld = fehler?.feld ?? null;
  return (
    <dialog ref={dialog} className="gv-dialog" aria-labelledby={`${id}-titel`} aria-describedby={`${id}-text`} onKeyDown={beiTaste}>
      <form className="gv-dialog-form" onSubmit={(e) => void absenden(e)} noValidate>
        <h4 id={`${id}-titel`} className="gv-dialog-titel">
          {b.titel}
        </h4>
        <p id={`${id}-text`} className="gv-text">
          {b.beschreibung}
        </p>
        {b.warnungen.map((w) => (
          <p key={w} className="gv-warnung">
            {w}
          </p>
        ))}
        {b.fingerabdruck && (
          <div className="gv-fp-box">
            <span className="gv-label">Fingerabdruck von {geraet.kennung}</span>
            <Fingerabdruck fp={b.fingerabdruck} />
          </div>
        )}
        {b.vergleichNoetig && (
          <label className="gv-check">
            <input
              ref={verglichenRef}
              type="checkbox"
              checked={eingabe.verglichen}
              aria-invalid={fehlerFeld === "verglichen" || undefined}
              aria-describedby={fehlerFeld === "verglichen" ? `${id}-fehler` : undefined}
              onChange={(e) => setEingabe((alt) => ({ ...alt, verglichen: e.target.checked }))}
            />
            <span>Ich habe den Fingerabdruck mit dem Fingerabdruck am Laptop verglichen, er stimmt überein.</span>
          </label>
        )}
        {b.grundNoetig && (
          <div className="gv-feld">
            <label htmlFor={`${id}-grund`}>Grund (wird dem Besitzer gezeigt und im Verlauf gespeichert)</label>
            <textarea
              id={`${id}-grund`}
              ref={grundRef}
              value={eingabe.grund}
              maxLength={GRUND_MAX}
              rows={2}
              readOnly={beschaeftigt}
              aria-invalid={fehlerFeld === "grund" || undefined}
              aria-describedby={fehlerFeld === "grund" ? `${id}-fehler` : undefined}
              onChange={(e) => setEingabe((alt) => ({ ...alt, grund: e.target.value }))}
            />
            <span className="gv-zaehler" aria-hidden="true">
              {eingabe.grund.length} von {GRUND_MAX} Zeichen
            </span>
          </div>
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
            value={eingabe.code}
            readOnly={beschaeftigt}
            aria-invalid={fehlerFeld === "code" || undefined}
            aria-describedby={`${id}-codehilfe${fehlerFeld === "code" ? ` ${id}-fehler` : ""}`}
            onChange={(e) => setEingabe((alt) => ({ ...alt, code: e.target.value }))}
          />
          <span id={`${id}-codehilfe`} className="gv-zaehler">
            Jede Änderung an einem Gerät braucht einen frischen Code, auch wenn du dich gerade angemeldet hast.
          </span>
        </div>
        <p id={`${id}-fehler`} ref={fehlerRef} className="gv-fehler" role="alert" tabIndex={-1}>
          {fehler?.text ?? ""}
        </p>
        <div className="gv-dialog-knoepfe">
          <button type="button" className="btn-secondary" disabled={beschaeftigt} onClick={onAbbrechen}>
            Abbrechen
          </button>
          <button type="submit" className="btn-primary" disabled={beschaeftigt} aria-busy={beschaeftigt || undefined}>
            {beschaeftigt ? "Einen Moment …" : b.knopf}
          </button>
        </div>
      </form>
    </dialog>
  );
}

// Stand der Liste in einem Store außerhalb der Komponente (wie bei den Hinweisen): Laden und Anzeigen bleiben getrennt,
// und eine spät eintreffende Antwort trägt nichts mehr ein, wenn die Karte inzwischen zu ist.
type ListenStand = { liste: Geraet[] | null; lage: "laedt" | "ok" | "fehler"; ladefehler: string };
const LEER: ListenStand = { liste: null, lage: "laedt", ladefehler: "" };
const stand = createStore<ListenStand>(LEER);
let runde = 0; // gegen Überholen: nur die Antwort der letzten Anfrage zählt

async function laden() {
  const meine = ++runde;
  try {
    const neu = await geraeteLaden();
    if (meine === runde) stand.set(() => ({ liste: neu, lage: "ok", ladefehler: "" }));
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

/**
 * Karte "Geräteverwaltung" (Geräte und PC, nur Admin): alle persönlichen Geräte aller Profile. Wartende Geräte stehen
 * oben, dann aktive und gesperrte; widerrufene sind eingeklappt. Entschieden wird nur über den Bestätigungsdialog. Nach
 * jeder Aktion wird die Liste neu geladen; die Meldung bleibt stehen und bekommt den Fokus.
 */
export const GeraeteVerwaltung = memo(function GeraeteVerwaltung() {
  const kennungHier = useSelect(geraeteAnzeige, (s) => s.kennung);
  const liste = useSelect(stand, (s) => s.liste);
  const lage = useSelect(stand, (s) => s.lage);
  const ladefehler = useSelect(stand, (s) => s.ladefehler);
  const [meldung, setMeldung] = useState<Meldung | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const meldungRef = useRef<HTMLParagraphElement>(null);
  const ausloeser = useRef<HTMLElement | null>(null);
  const dialogOffen = useRef(false);

  useEffect(() => {
    void laden();
    const uhr = setInterval(() => {
      if (document.visibilityState === "visible" && !dialogOffen.current) void laden();
    }, AUFFRISCHEN_MS);
    return () => {
      clearInterval(uhr);
      leeren();
    };
  }, []);

  const oeffnen = (g: Geraet, aktion: Aktion, knopf: HTMLButtonElement) => {
    ausloeser.current = knopf;
    dialogOffen.current = true;
    setMeldung(null);
    setDialog({ geraet: g, aktion, hier: istDieserLaptop(g, kennungHier) });
  };

  const schliessen = () => {
    dialogOffen.current = false;
    setDialog(null);
  };

  const abbrechen = () => {
    schliessen();
    // Fokus zurück auf den Knopf, der den Dialog geöffnet hat (die Liste hat sich nicht verändert)
    const knopf = ausloeser.current;
    setTimeout(() => (knopf?.isConnected ? knopf.focus() : meldungRef.current?.focus()), 0);
  };

  const fertig = (m: Meldung, neuLaden: boolean) => {
    schliessen();
    setMeldung(m);
    // Die Karte, die den Dialog geöffnet hat, ändert sich oder verschwindet: der Fokus geht auf die Meldung
    setTimeout(() => meldungRef.current?.focus(), 0);
    if (neuLaden) void laden();
  };

  const sortiert = liste ? sortiere(liste) : [];
  const warten = wartende(sortiert);
  const uebrige = sortiert.filter((g) => g.status === "aktiv" || g.status === "gesperrt");
  const widerrufene = sortiert.filter((g) => g.status === "widerrufen");
  const zeige = (g: Geraet) => <GeraeteKarte key={g.kennung} g={g} hier={istDieserLaptop(g, kennungHier)} onAktion={oeffnen} />;

  return (
    <div className="gv" id={GERAETEVERWALTUNG_ID} tabIndex={-1}>
      <p className="ez-text">
        Hier gibst du die Geräte der Profile frei, sperrst sie oder entziehst ihnen den Zugang. Jede Änderung braucht den
        aktuellen Code deiner Authenticator-App und wird im Verlauf des Geräts festgehalten.
      </p>
      <div className="gv-kopf">
        <p ref={meldungRef} className="gv-meldung" data-art={meldung?.art} role="status" tabIndex={-1}>
          {meldung?.text ?? ""}
        </p>
        <button type="button" className="btn-secondary btn-small" onClick={() => void laden()}>
          Liste neu laden
        </button>
      </div>

      {lage === "laedt" && !liste && (
        <p className="ez-leer" role="status">
          Die Geräte werden geladen …
        </p>
      )}
      {lage === "fehler" && (
        <p className="gv-meldung" data-art="fehler" role="alert">
          {liste ? "Die Liste konnte nicht aktualisiert werden, sie zeigt den Stand von vorher. " : ""}
          {ladefehler}
        </p>
      )}
      {liste && liste.length === 0 && lage !== "fehler" && (
        <p className="ez-leer" role="status">
          Es sind noch keine Geräte eingetragen. Ein Laptop meldet sich bei der ersten Anmeldung in der Desktop-App.
        </p>
      )}

      {warten.length > 0 && (
        <section className="gv-gruppe" aria-label="Wartende Geräte">
          <h5 className="gv-gruppe-titel">Wartet auf Freigabe ({warten.length})</h5>
          <ul className="gv-liste">{warten.map(zeige)}</ul>
        </section>
      )}
      {uebrige.length > 0 && (
        <section className="gv-gruppe" aria-label="Aktive und gesperrte Geräte">
          <h5 className="gv-gruppe-titel">Geräte ({uebrige.length})</h5>
          <ul className="gv-liste">{uebrige.map(zeige)}</ul>
        </section>
      )}
      {widerrufene.length > 0 && (
        <details className="gv-widerrufen">
          <summary>Widerrufene Geräte der letzten 90 Tage ({widerrufene.length})</summary>
          <ul className="gv-liste">{widerrufene.map(zeige)}</ul>
        </details>
      )}

      {dialog && <BestaetigungsDialog key={`${dialog.geraet.kennung}-${dialog.aktion}`} daten={dialog} onFertig={fertig} onAbbrechen={abbrechen} />}
    </div>
  );
});
