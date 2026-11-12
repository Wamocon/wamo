"use client";

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useSelect } from "@/lib/store";
import { wamo } from "@/lib/wamo";
import {
  aktualisierungHinweis,
  anordnung,
  beschriftung,
  bueroAnsage,
  darstellung,
  gruppenName,
  KARTE,
  LEER_OHNE_GERAETE,
  leerFehler,
  nachGruppen,
  prozent,
  ringe,
  uhrzeitKurz,
  unbekanntHinweis,
  zusammenfassung,
  type Ampel,
  type BueroGeraet,
  type Leer,
} from "./bueroKarteModell";
import { BueroSchalter } from "./BueroSchalter";
import { useBueroStand } from "./useBueroStand";

/**
 * Büro-Karte: Radialansicht aller gemeinsamen Bürogeräte. In der Mitte WAMO (Sokrates-2), je Gerätegruppe ein Ring, je
 * Gerät ein Punkt. Zustand nie nur über Farbe: in Ordnung = weißer, gefüllter Kreis, Störung = roter Kreis mit Strich und
 * langsamem Pulsieren (entfällt bei reduzierter Bewegung), unbekannt = grauer, hohler Kreis mit gestricheltem Rand.
 *
 * Jeder Punkt ist ein Button (Tab, Enter, Leertaste); ein Klick öffnet eine Detailkarte mit Zustand in Klartext und
 * Sprechtipp. Admins finden dort auch die Knöpfe zum Schalten (BueroSchalter.tsx), für alle anderen bleibt es bei der Ansicht und
 * der Sprache. Unter der Zeichnung steht dieselbe
 * Auskunft als Liste (Textalternative). Daten und Prüfung: bueroKarteModell.ts, Abruf: useBueroStand.ts.
 *
 * Gibt es Geräte im Netz, die WAMO nicht kennt, steht ein ruhiger Hinweis mit der Zahl da (grau, kein Alarmrot, nur die Zahl,
 * nie Namen oder Adressen). Die Liste fragt ein Admin per Sprache ab; nur ihm nennt der Hinweis den Sprechtipp.
 */

/** Zustandszeichen: derselbe Kreis in Karte, Legende, Liste und Detailkarte. Mit `halo` pulsiert die Störung. */
function Zeichen({ ampel, halo = false, klasse = "bk-zeichen" }: { ampel: Ampel; halo?: boolean; klasse?: string }) {
  const form = darstellung(ampel).form;
  return (
    // Mit Halo ist der Ausschnitt größer (Platz für das Pulsieren); beide Fassungen zeigen den Kreis gleich groß (1 Einheit = 1 px)
    <svg className={klasse} viewBox={halo ? "-16 -16 32 32" : "-9 -9 18 18"} aria-hidden="true" focusable="false">
      {halo && form === "strich" && <circle className="bk-halo" r="7" />}
      <circle className={`bk-kreis bk-kreis-${form}`} r="7" />
      {form === "strich" && <path className="bk-strich" d="M0 -3.4V1.2M0 3.9V4" />}
    </svg>
  );
}

function Leerzustand({ leer }: { leer: Leer }) {
  return (
    <div className="bk-leer">
      <p className="bk-leer-titel">{leer.titel}</p>
      <p className="bk-leer-text">{leer.text}</p>
    </div>
  );
}

function Legende() {
  const ampeln: Ampel[] = ["ok", "stoerung", "unbekannt"];
  return (
    <ul className="bk-legende" aria-label="Bedeutung der Zeichen">
      {ampeln.map((a) => (
        <li key={a}>
          <Zeichen ampel={a} />
          <span>{darstellung(a).wort}</span>
        </li>
      ))}
    </ul>
  );
}

function Detail({ geraet, onSchliessen }: { geraet: BueroGeraet; onSchliessen: () => void }) {
  const d = darstellung(geraet.ampel);
  const ref = useRef<HTMLElement>(null);
  // Am Handy liegt die Karte sonst unter dem sichtbaren Bereich: nur bei Bedarf ins Bild holen
  useEffect(() => {
    const kurz = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    ref.current?.scrollIntoView({ block: "nearest", behavior: kurz ? "auto" : "smooth" });
  }, [geraet.id]);
  return (
    <section ref={ref} id="bk-detail" className="bk-detail" data-ampel={geraet.ampel} aria-labelledby="bk-detail-name">
      <div className="bk-detail-kopf">
        <Zeichen ampel={geraet.ampel} klasse="bk-zeichen bk-zeichen-gross" />
        <h3 id="bk-detail-name" className="bk-detail-name">
          {geraet.name}
        </h3>
        <button type="button" className="bk-zu" onClick={onSchliessen} aria-label={`Detailkarte zu ${geraet.name} schließen`}>
          <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
            <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" />
          </svg>
        </button>
      </div>
      <p className="bk-detail-zustand">
        <strong>{d.wort}</strong>
        <span>{geraet.text}</span>
      </p>
      <BueroSchalter key={geraet.id} geraet={geraet} />
      {geraet.tipp && (
        <p className="bk-tipp">
          <span className="bk-etikett">Sprechtipp</span>
          {geraet.tipp}
        </p>
      )}
      <p className="bk-detail-gruppe">{gruppenName(geraet.gruppe)}</p>
    </section>
  );
}

function Liste({ geraete }: { geraete: readonly BueroGeraet[] }) {
  return (
    <section className="bk-liste" aria-labelledby="bk-liste-h">
      <h3 id="bk-liste-h" className="bk-liste-titel">
        Alle Geräte als Liste
      </h3>
      {nachGruppen(geraete).map((gr) => (
        <div key={gr.id} className="bk-gruppe">
          <h4 className="bk-gruppe-titel">{gr.label}</h4>
          <ul className="bk-zeilen">
            {gr.geraete.map((g) => (
              <li key={g.id} className="bk-zeile" data-ampel={g.ampel}>
                <Zeichen ampel={g.ampel} />
                <span className="bk-zeile-text">
                  <span className="bk-zeile-name">{g.name}</span>
                  <span className="bk-zeile-zustand">
                    {darstellung(g.ampel).wort}: {g.text}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}

/** Ruhiger Hinweis auf unbekannte Geräte im Netz: grauer, hohler Kreis (wie "unbekannt"), Text, bei Admins der Sprechtipp. */
function UnbekanntHinweis({ anzahl, istAdmin }: { anzahl: number | null; istAdmin: boolean }) {
  const hinweis = unbekanntHinweis(anzahl, istAdmin);
  if (!hinweis) return null;
  return (
    <div className="bk-unbekannt">
      <p className="bk-unbekannt-text">
        <Zeichen ampel="unbekannt" />
        <span>{hinweis.text}</span>
      </p>
      {hinweis.tipp && (
        <p className="bk-tipp">
          <span className="bk-etikett">Sprechtipp</span>
          {hinweis.tipp}
        </p>
      )}
    </div>
  );
}

export function BueroKarte() {
  const { stand, fehler, geladen } = useBueroStand();
  const istAdmin = useSelect(wamo, (s) => s.profil?.rolle === "admin");
  const [gewaehlt, setGewaehlt] = useState<string | null>(null);
  const knoepfe = useRef<Map<string, HTMLButtonElement>>(new Map());
  const titelId = useId();

  const geraete = useMemo(() => stand?.geraete ?? [], [stand]);
  const nachId = useMemo(() => new Map(geraete.map((g) => [g.id, g])), [geraete]);
  const punkte = useMemo(() => anordnung(geraete), [geraete]);
  const kreise = useMemo(() => ringe(geraete), [geraete]);
  // Verschwindet das gewählte Gerät aus den Daten, schließt die Detailkarte (Zustand beim Rendern anpassen). Sonst ginge sie bei
  // seiner Rückkehr von selbst wieder auf, und die Auswahl zeigte auf ein Gerät, das es nicht mehr gibt.
  if (gewaehlt !== null && !nachId.has(gewaehlt)) setGewaehlt(null);
  const aktuell = gewaehlt !== null ? (nachId.get(gewaehlt) ?? null) : null;

  const schliessen = () => {
    const id = gewaehlt;
    setGewaehlt(null);
    if (id) knoepfe.current.get(id)?.focus();
  };

  const taste = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === "Escape" && aktuell) {
      e.stopPropagation();
      schliessen();
    }
  };

  const zeit = stand ? uhrzeitKurz(stand.stand) : "";

  return (
    <section className="bk" aria-labelledby={titelId} onKeyDown={taste}>
      {/* Der eine Bereich für Bildschirmleser: immer eingehängt, nur der Text wechselt (ein neu eingefügter Bereich wird oft überhört).
          Die sichtbaren Zeilen darunter sind keine eigenen Ansagen. */}
      <p className="sr-only" role="status" aria-atomic="true">
        {bueroAnsage({ geladen, stand, fehler })}
      </p>
      <header className="bk-kopf">
        <h2 id={titelId} className="bk-titel">
          Büro
        </h2>
        {stand && stand.geraete.length > 0 && <p className="bk-zusammen">{zusammenfassung(geraete)}</p>}
      </header>

      {!geladen && <p className="bk-leer-titel bk-laedt">Geräte werden geladen …</p>}
      {geladen && !stand && <Leerzustand leer={leerFehler(fehler)} />}
      {stand && geraete.length === 0 && <Leerzustand leer={LEER_OHNE_GERAETE} />}

      {geraete.length > 0 && (
        <>
          <div className="bk-karte" role="group" aria-label="Büro-Karte: Geräte in Ringen um WAMO">
            <svg className="bk-svg" viewBox={`0 0 ${KARTE.groesse} ${KARTE.groesse}`} aria-hidden="true" focusable="false">
              {kreise.map((r) => (
                <circle key={r.id} className="bk-ring" cx={KARTE.mitte} cy={KARTE.mitte} r={r.radius} />
              ))}
              <circle className="bk-mitte" cx={KARTE.mitte} cy={KARTE.mitte} r={KARTE.mitteRadius} />
              <text className="bk-mitte-text" x={KARTE.mitte} y={KARTE.mitte} textAnchor="middle" dominantBaseline="central">
                WAMO
              </text>
            </svg>
            {punkte.map((p) => {
              const g = nachId.get(p.id);
              if (!g) return null;
              const offen = gewaehlt === g.id;
              return (
                <button
                  key={g.id}
                  type="button"
                  ref={(el) => {
                    if (el) knoepfe.current.set(g.id, el);
                    else knoepfe.current.delete(g.id);
                  }}
                  className="bk-punkt"
                  style={{ left: `${prozent(p.x)}%`, top: `${prozent(p.y)}%` }}
                  data-ampel={g.ampel}
                  data-h={p.x < KARTE.mitte ? "l" : "r"}
                  data-v={p.y < KARTE.mitte ? "u" : "o"}
                  aria-label={beschriftung(g)}
                  aria-expanded={offen}
                  aria-controls={offen ? "bk-detail" : undefined}
                  onClick={() => setGewaehlt(offen ? null : g.id)}
                >
                  <Zeichen ampel={g.ampel} halo klasse="bk-zeichen bk-zeichen-punkt" />
                  <span className="bk-name" aria-hidden="true">
                    {g.name}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="bk-detail-wrap" aria-live="polite" aria-atomic="false" aria-relevant="additions">
            {aktuell && <Detail geraet={aktuell} onSchliessen={schliessen} />}
          </div>

          <Legende />
          <p className="bk-hinweis">
            Mitte: WAMO auf Sokrates-2. Ringe von innen nach außen: {kreise.map((r) => r.label).join(", ")}.
          </p>
          <UnbekanntHinweis anzahl={stand?.unbekannt ?? null} istAdmin={istAdmin} />

          {stand && fehler !== null && (
            <p className="bk-stand bk-stand-alt">
              {aktualisierungHinweis(fehler, zeit)}
            </p>
          )}
          {stand && fehler === null && zeit && <p className="bk-stand">Stand {zeit} Uhr, aktualisiert sich alle 30 Sekunden</p>}

          <Liste geraete={geraete} />
        </>
      )}
    </section>
  );
}
