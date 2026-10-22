"use client";

import { memo, useEffect } from "react";
import { alsHinweise, kernJson, type Hinweis } from "@/lib/kernApi";
import { createStore, useSelect } from "@/lib/store";
import { zeigeToast } from "@/lib/wamo";

/**
 * Hinweise an das eigene Profil (voice-core/hinweise_api.py, Tabelle hinweise): geteilte Arbeit, Änderungen an
 * eigenen Geräten und für Admins neue oder wartende Geräte. Höchstens drei Karten oben in der Präsenzspalte, darunter
 * die Zahl der weiteren. Abfrage beim Start, jede Minute (nur bei sichtbarem Fenster) und wenn das Fenster wieder
 * sichtbar wird; neue Hinweise werden angesagt. Ist der Kern nicht erreichbar, bleibt der Bereich still leer.
 * Quittierte Hinweise verschwinden sofort und kommen auch durch eine ältere, später eintreffende Antwort nicht zurück.
 *
 * Der Stand liegt in einem Store, den nur die Karten oben in der Hauptansicht (Hinweise) befüllen. Die Einstellungsseite
 * (Info, nur für Admins) zeigt dieselbe Liste vollständig (HinweiseListe), ohne ein zweites Mal zu laden. Wer abmeldet,
 * leert den Store.
 */

const INTERVALL_MS = 60_000;
const MAX_KARTEN = 3;

const ART_TEXT: Record<string, string> = {
  geraet_gebunden: "Gerät",
  geraet_beantragt: "Gerät wartet",
  geraet_konflikt: "Gerät",
  geraet_gesperrt: "Gerät gesperrt",
  geraet_entsperrt: "Gerät",
  geraet_widerrufen: "Gerät widerrufen",
  teilung_erhalten: "Geteilt",
  teilung_geaendert: "Geteilt",
  teilung_widerrufen: "Geteilt",
  scan_fertig: "Scan",
};

type Stand = { liste: Hinweis[]; /** laedt: noch kein Abruf gelungen; fehler: der letzte Abruf ging schief (die Liste ist dann ein älterer Stand) */ lage: "laedt" | "ok" | "fehler" };
const KEINE: Stand = { liste: [], lage: "laedt" };
const offene = createStore<Stand>(KEINE);
const stand = {
  runde: 0, // gegen Überholen: nur die Antwort der letzten Anfrage zählt
  quittiert: new Set<string>(),
  bekannt: null as Set<string> | null, // null: noch nie geladen (nichts ansagen)
};

function leeren() {
  stand.runde++; // eine noch laufende Abfrage darf nichts mehr eintragen
  stand.quittiert.clear();
  stand.bekannt = null;
  offene.set(() => KEINE);
}

async function laden() {
  const meine = ++stand.runde;
  try {
    const daten = (await kernJson("/hinweise")) as { hinweise?: unknown };
    if (meine !== stand.runde) return;
    const offen = alsHinweise(daten?.hinweise).filter((h) => !h.gelesen && !stand.quittiert.has(h.id));
    const neu = stand.bekannt ? offen.filter((h) => !stand.bekannt?.has(h.id)) : [];
    stand.bekannt = new Set(offen.map((h) => h.id));
    if (neu.length === 1) zeigeToast(`Neuer Hinweis: ${neu[0].titel}`);
    else if (neu.length > 1) zeigeToast(`${neu.length} neue Hinweise`);
    offene.set(() => ({ liste: offen, lage: "ok" }));
  } catch {
    // Kern ohne Schnittstelle (älterer Stand) oder gerade nicht erreichbar: die Karten oben bleiben still
    if (meine === stand.runde) offene.set((alt) => (alt.lage === "fehler" ? alt : { ...alt, lage: "fehler" }));
  }
}

async function gelesen(ids: string[]) {
  ids.forEach((id) => stand.quittiert.add(id));
  stand.runde++; // eine gerade laufende Abfrage darf sie nicht zurückholen
  offene.set((alt) => ({ ...alt, liste: alt.liste.filter((x) => !ids.includes(x.id)) }));
  const fehlgeschlagen = await Promise.allSettled(
    ids.map((id) => kernJson(`/hinweise/${encodeURIComponent(id)}/gelesen`, "POST")),
  );
  if (fehlgeschlagen.some((r) => r.status === "rejected")) {
    ids.forEach((id) => stand.quittiert.delete(id)); // nicht quittiert: beim nächsten Laden wieder da
    void laden();
  }
}

export const Hinweise = memo(function Hinweise() {
  const hinweise = useSelect(offene, (s) => s.liste);

  useEffect(() => {
    void laden();
    const uhr = setInterval(() => {
      if (document.visibilityState === "visible") void laden();
    }, INTERVALL_MS);
    const sichtbar = () => {
      if (document.visibilityState === "visible") void laden();
    };
    document.addEventListener("visibilitychange", sichtbar);
    return () => {
      clearInterval(uhr);
      document.removeEventListener("visibilitychange", sichtbar);
      leeren();
    };
  }, []);

  if (!hinweise.length) return null;
  const sichtbar = hinweise.slice(0, MAX_KARTEN);
  const weitere = hinweise.length - sichtbar.length;
  return (
    <section className="hinweise" aria-label="Hinweise">
      {sichtbar.map((h) => (
        <HinweisKarte key={h.id} h={h} />
      ))}
      <div className="hinweise-fuss">
        {weitere > 0 && <span className="hinweise-weitere">und {weitere} weitere</span>}
        {sichtbar.length > 1 && (
          <button type="button" className="error-close" onClick={() => void gelesen(sichtbar.map((h) => h.id))}>
            Diese {sichtbar.length} als gelesen markieren
          </button>
        )}
      </div>
    </section>
  );
});

function HinweisKarte({ h }: { h: Hinweis }) {
  return (
    <article className="hinweis-karte" data-art={h.art}>
      <p className="eyebrow">{ART_TEXT[h.art] ?? "Hinweis"}</p>
      <p className="hinweis-titel">{h.titel}</p>
      {h.text && <p className="hinweis-text">{h.text}</p>}
      <button type="button" className="error-close" aria-label={`Gelesen: ${h.titel}`} onClick={() => void gelesen([h.id])}>
        Gelesen
      </button>
    </article>
  );
}

/** Alle offenen Hinweise ohne Begrenzung (Einstellungsseite, Info). Lädt nicht selbst, sondern zeigt den Stand der Hauptansicht. */
export const HinweiseListe = memo(function HinweiseListe() {
  const hinweise = useSelect(offene, (s) => s.liste);
  const lage = useSelect(offene, (s) => s.lage);
  if (!hinweise.length) {
    return (
      <p className="ez-leer" role="status">
        {lage === "laedt"
          ? "Hinweise werden geladen …"
          : lage === "fehler"
            ? "Die Hinweise sind gerade nicht erreichbar."
            : "Keine offenen Hinweise."}
      </p>
    );
  }
  return (
    <div className="hinweise">
      {hinweise.map((h) => (
        <HinweisKarte key={h.id} h={h} />
      ))}
    </div>
  );
});

/** Eine Zeile mit der Zahl offener Hinweise für die Meldungen der Einstellungen; die Karten selbst stehen im Gespräch. */
export const HinweiseZaehler = memo(function HinweiseZaehler({ onAnsehen }: { onAnsehen: () => void }) {
  const anzahl = useSelect(offene, (s) => s.liste.length);
  if (!anzahl) return null;
  return (
    <p className="hinweis-toast" role="status">
      <span className="dot dot-rot" aria-hidden="true" />
      {anzahl === 1 ? "Ein offener Hinweis" : `${anzahl} offene Hinweise`}
      <button type="button" className="error-close" onClick={onAnsehen}>
        Im Gespräch ansehen
      </button>
    </p>
  );
});
