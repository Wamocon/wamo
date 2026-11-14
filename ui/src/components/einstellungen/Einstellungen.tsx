"use client";

import { useEffect, useRef, type MouseEvent, type ReactNode } from "react";
import { GeteiltBereich } from "@/components/Geteilt";
import { ModellZugang } from "@/components/ModellZugang";
import { offeneEntscheidungen } from "@/components/kommando/ansicht";
import {
  abschnitteFuer,
  abschnittTitel,
  hashFuer,
  wirksamerAbschnitt,
  zurueckZiel,
  type Abschnitt,
  type AbschnittId,
} from "@/lib/einstellungenAbschnitte";
import {
  abschnittWaehlen,
  einstellungenSchliessen,
  useEinstellungenZiel,
  useZweispaltig,
  zurListe,
} from "@/lib/einstellungenHash";
import { laborAnsicht } from "@/lib/laborPlatz";
import { useSelect } from "@/lib/store";
import type { WamoAktionen } from "@/lib/useWamo";
import { wamo } from "@/lib/wamo";
import { DarstellungAbschnitt, InfoAbschnitt, KontoAbschnitt, SpracheAbschnitt } from "./Abschnitte";
import { StimmeAbschnitt } from "./StimmeAbschnitt";
import { Block } from "./Block";
import { Fernzugriff } from "./Fernzugriff";
import { GeraeteAbschnitt } from "./Geraete";
import { VerbrauchAbschnitt } from "./Verbrauch";
import { AenderungenAbschnitt } from "./Aenderungen";
import { GedaechtnisAbschnitt } from "./Gedaechtnis";

/**
 * Die Einstellungsseite: eine Ansicht der Hauptseite, kein eigener Pfad (siehe lib/einstellungenAbschnitte.ts). Ab 860 px
 * zwei Spalten, links die Gliederung, rechts der gewählte Abschnitt. Darunter eine Liste, die in den Abschnitt führt. Der
 * Abschnitt steht im Hash (#einstellungen/modellzugang). Das Gespräch läuft im Hintergrund weiter: die Kommandozentrale
 * bleibt eingehängt und ist nur verborgen (kommando/Rahmen.tsx).
 *
 * Bedienung: Beim Öffnen und bei jedem Wechsel springt der Fokus auf die Überschrift des Abschnitts (am Handy in der
 * Liste auf "Einstellungen"). Escape und "Zum Gespräch" schließen; am Handy führt Escape aus einem Abschnitt zuerst zur
 * Liste. Escape lässt Eingaben in Texteingaben in Ruhe (Editor, Schlüsselfelder), damit nichts verloren geht. Meldungen
 * der Kommandozentrale (Fehler, Gemerktes, Hinweise) stehen oben in einem eigenen Streifen, weil die Kommandozentrale
 * verborgen ist und ein Alarm dort nicht vorgelesen würde. Wartet etwas auf eine Entscheidung, steht dort auch der Weg
 * zurück, denn Freigaben erscheinen nur in der Kommandozentrale.
 */

type Props = {
  aktionen: WamoAktionen;
  email: string | null;
  onAbmelden: () => void;
  /** Meldungen der Kommandozentrale (Fehler, Gemerktes, Hinweise, kurze Bestätigungen), die hier sonst unsichtbar wären */
  meldungen: ReactNode;
};

/** Ist gerade ein Popover (Profil, Status) offen? Ältere Browser kennen den Selektor nicht; dann gilt: nein. */
function popoverOffen(): boolean {
  try {
    return document.querySelector(":popover-open") !== null;
  } catch {
    return false;
  }
}

/** Ist ein modales Dialogfenster offen? Dann schließt Escape nur dieses, nicht die Seite. */
function dialogOffen(): boolean {
  return document.querySelector("dialog[open]") !== null;
}

/**
 * Tippt die Person gerade in einem Feld, in dem Escape etwas anderes bedeutet oder etwas verloren ginge? Mehrzeilige
 * Texte (Editor für geteilte Dokumente) und Felder mit Inhalt (Schlüssel, Nachricht) schließen die Seite nicht.
 */
function tippt(ziel: EventTarget | null): boolean {
  if (!(ziel instanceof HTMLElement)) return false;
  if (ziel.isContentEditable || ziel instanceof HTMLTextAreaElement) return true;
  return ziel instanceof HTMLInputElement && ziel.value !== "" && !["checkbox", "radio", "button", "submit"].includes(ziel.type);
}

function Entscheidung() {
  const offen = useSelect(wamo, offeneEntscheidungen);
  if (offen === 0) return null;
  return (
    <div className="ez-meldung">
      <span className="dot dot-rot" aria-hidden="true" />
      <p>
        {offen === 1 ? "Eine Freigabe oder ein Auftrag wartet" : `${offen} Freigaben oder Aufträge warten`} auf deine
        Entscheidung.
      </p>
      <button type="button" className="btn-secondary btn-small" onClick={() => einstellungenSchliessen()}>
        Zum Gespräch
      </button>
    </div>
  );
}

export function Einstellungen({ aktionen, email, onAbmelden, meldungen }: Props) {
  const ziel = useEinstellungenZiel();
  const zweispaltig = useZweispaltig();
  const rolle = useSelect(wamo, (s) => s.profil?.rolle ?? null);
  // Laborkonten (lib/laborPlatz.ts): nur die Mikrofonwahl; ein Hash auf einen anderen Abschnitt führt dann zur Liste oder zur Mikrofonwahl
  const labor = laborAnsicht(email).einstellungen === "mikrofon";
  const erlaubt = abschnitteFuer({ rolle, labor });
  const wirksam = wirksamerAbschnitt(ziel, erlaubt, zweispaltig);
  const titel = wirksam ? abschnittTitel(wirksam) : "";

  const seitenTitel = useRef<HTMLHeadingElement>(null);
  const abschnittsTitel = useRef<HTMLHeadingElement>(null);
  const inhalt = useRef<HTMLDivElement>(null);

  // Fokus auf die Überschrift des Abschnitts (oder der Liste), Inhalt wieder nach oben. Nur ein Wechsel des Abschnitts
  // zählt: ein Drehen oder Zoomen (zweispaltig ändert sich) setzt den Fokus nicht zurück.
  const zweispaltigRef = useRef(zweispaltig);
  useEffect(() => {
    zweispaltigRef.current = zweispaltig;
  });
  useEffect(() => {
    (wirksam ? abschnittsTitel : seitenTitel).current?.focus({ preventScroll: true });
    inhalt.current?.scrollTo({ top: 0 });
    if (!zweispaltigRef.current) window.scrollTo({ top: 0 });
  }, [wirksam]);

  // Browserleiste und Screenreader: wo bin ich?
  useEffect(() => {
    const vorher = document.title;
    document.title = `${titel ? `${titel}, ` : ""}Einstellungen | W.A.I.S.`;
    return () => {
      document.title = vorher;
    };
  }, [titel]);

  // Escape wie "Zurück". Was sich selbst auf Escape schließt (Popover, geöffnete Formulare), geht vor.
  const zurueck = () => {
    if (zurueckZiel(wirksam, zweispaltig) === "liste") zurListe();
    else einstellungenSchliessen();
  };
  const zurueckRef = useRef(zurueck);
  useEffect(() => {
    zurueckRef.current = zurueck;
  });
  useEffect(() => {
    const beiTaste = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || e.isComposing) return;
      // ein offener Bestätigungsdialog (Geräteverwaltung) gehört sich selbst; der Fokus kann während einer Anfrage auf body liegen
      if (popoverOffen() || dialogOffen() || tippt(e.target)) return;
      e.preventDefault();
      zurueckRef.current();
    };
    document.addEventListener("keydown", beiTaste);
    return () => document.removeEventListener("keydown", beiTaste);
  }, []);

  const waehlen = (e: MouseEvent<HTMLAnchorElement>, id: AbschnittId) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    abschnittWaehlen(id);
  };

  const admin = rolle === "admin";
  const aktuell: Abschnitt | undefined = erlaubt.find((a) => a.id === wirksam);

  return (
    <main className="ez" data-ansicht={wirksam === null ? "liste" : "abschnitt"}>
      <Entscheidung />
      <div className="ez-meldungen">{meldungen}</div>
      <div className="ez-gitter">
        <div className="ez-seite">
          <div className="ez-kopf">
            <h2 ref={seitenTitel} tabIndex={-1} className="ez-titel">
              Einstellungen
            </h2>
            <button type="button" className="btn-secondary btn-small ez-zumgespraech" onClick={() => einstellungenSchliessen()}>
              Zum Gespräch
            </button>
          </div>
          <nav className="ez-nav" aria-label="Abschnitte der Einstellungen">
            <ul>
              {erlaubt.map((a) => (
                <li key={a.id}>
                  <a
                    href={hashFuer(a.id)}
                    className="ez-nav-link"
                    aria-current={a.id === wirksam ? "page" : undefined}
                    onClick={(e) => waehlen(e, a.id)}
                  >
                    <span className="ez-nav-titel">{a.titel}</span>
                    <span className="ez-nav-kurz">{a.kurz}</span>
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        </div>

        <div ref={inhalt} className="ez-inhalt">
          {aktuell && wirksam && (
            <section className="ez-abschnitt" aria-labelledby="ez-h-abschnitt">
              <div className="ez-abschnitt-kopf">
                {!zweispaltig && (
                  <div className="ez-wege">
                    <button type="button" className="ez-zurueck" onClick={zurListe}>
                      <svg viewBox="0 0 16 16" aria-hidden="true">
                        <path d="M10 3L5 8l5 5" />
                      </svg>
                      Alle Einstellungen
                    </button>
                    <button type="button" className="ez-zurueck" onClick={() => einstellungenSchliessen()}>
                      Zum Gespräch
                    </button>
                  </div>
                )}
                <h3 id="ez-h-abschnitt" ref={abschnittsTitel} tabIndex={-1} className="ez-abschnitt-titel">
                  {aktuell.titel}
                </h3>
                <p className="ez-abschnitt-kurz">{aktuell.kurz}</p>
              </div>
              <div className="ez-abschnitt-inhalt" key={wirksam}>
                {wirksam === "konto" && <KontoAbschnitt email={email} onAbmelden={onAbmelden} />}
                {wirksam === "modellzugang" && <ModellZugang onAnfrage={aktionen.modellzugang} />}
                {wirksam === "verbrauch" && <VerbrauchAbschnitt />}
                {wirksam === "sprache" && (
                  <SpracheAbschnitt mikrofone={aktionen.mikrofone} mikrofonWaehlen={aktionen.mikrofonWaehlen} nurMikrofon={labor} />
                )}
                {wirksam === "stimme" && <StimmeAbschnitt aktionen={aktionen.stimmabdruck} />}
                {wirksam === "gedaechtnis" && <GedaechtnisAbschnitt />}
                {wirksam === "darstellung" && <DarstellungAbschnitt />}
                {wirksam === "geraete" && <GeraeteAbschnitt onPcSteuerung={aktionen.pcSteuerung} />}
                {wirksam === "fernzugriff" && (
                  <Block titel="Desktop der Sparks">
                    <Fernzugriff />
                  </Block>
                )}
                {wirksam === "aenderungen" && <AenderungenAbschnitt />}
                {wirksam === "teilen" && (
                  <Block titel="Geteilte Arbeit">
                    <GeteiltBereich />
                  </Block>
                )}
                {wirksam === "info" && <InfoAbschnitt admin={admin} />}
              </div>
            </section>
          )}
        </div>
      </div>
    </main>
  );
}
