"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { abgeschlossenText, ansicht, type Diff, type Status, type Wirkung } from "@/lib/aenderungen";
import { statusLaden } from "@/lib/aenderungenApi";
import { AenderungenFreigabe, type Ausgang } from "./AenderungenFreigabe";
import { Block, Daten } from "./Block";

/**
 * Abschnitt "Änderungen" (nur Admin): der Stand der Selbstüberarbeitung (Konzept docs/selbstueberarbeitung.md, Schritt 1.5). Die Karte liest den
 * Stand des Release-Dienstes über den Kern (GET /wamo/api/release/status) und fragt alle 10 Sekunden neu, solange der Abschnitt offen und der Tab
 * sichtbar ist. Sie ändert nichts: Anfragen vorschlagen kommt mit den Werkzeugen des Kerns (Schritt 3.4), Freigeben geht allein über den
 * Freigabe-Dienst des Ankers mit frischem zweiten Faktor, sobald der Release-Dienst Diff und Versionsvorschlag liefert.
 *
 * Jede Lage steht als Text da; Form und Farbe sind nur Beigabe (die Seite verlässt sich nie auf Farbe allein).
 */

const INTERVALL_MS = 10_000;

const LAGE_KENNZEICHEN = {
  nicht_erreichbar: "Nicht erreichbar",
  stoerung: "Störung",
  gesperrt: "Gesperrt",
  wartet: "Wartet auf dich",
  arbeit: "In Arbeit",
  bereit: "Bereit",
} as const;

const ZUSTAND_TEXT = { fertig: "fertig", aktuell: "jetzt", offen: "offen" } as const;
const DATEI_STATUS = { A: "neu", M: "geändert", D: "gelöscht", T: "Art geändert" } as const;
const ART_TEXT: Record<string, string> = {
  prozess: "Prozesse starten",
  netz: "Netzzugriffe",
  datei: "Dateizugriffe",
  geheimnis: "Geheimnisse und Umgebung",
  datenbank: "Datenbank",
  rechte: "Rechte und Dienste",
};

/** Geänderte Dateien als Tabelle: Pfade kommen aus Git und sind bereinigt, Anzeige nur als Text (React maskiert). */
function DiffTabelle({ diff }: { diff: Diff }) {
  return (
    <div className="ae-diff">
      <p className="ez-klein">
        {diff.anzahl} {diff.anzahl === 1 ? "Datei" : "Dateien"} geändert, {diff.hinzu} Zeilen neu, {diff.weg} Zeilen weg
        {diff.binaer > 0 ? `, ${diff.binaer} Binärdateien` : ""}.
      </p>
      <table className="ae-tabelle">
        <caption className="ae-unter">Geänderte Dateien</caption>
        <thead>
          <tr>
            <th scope="col">Datei</th>
            <th scope="col">Art</th>
            <th scope="col">Neu</th>
            <th scope="col">Weg</th>
          </tr>
        </thead>
        <tbody>
          {diff.dateien.map((d, i) => (
            <tr key={`${d.p}-${i}`}>
              <th scope="row" className="num">
                {d.p}
              </th>
              <td>{DATEI_STATUS[d.s]}</td>
              <td className="num">{d.a ?? "–"}</td>
              <td className="num">{d.d ?? "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {(diff.gekappt || diff.anzahl > diff.dateien.length) && (
        <p className="ez-klein">Die Liste zeigt nur die ersten {diff.dateien.length} Dateien.</p>
      )}
    </div>
  );
}

/** Wirkungsflächen: wo die Änderung etwas Wichtiges berührt (Prozesse, Netz, Geheimnisse ...). */
function WirkungBlock({ w }: { w: Wirkung }) {
  const arten = Object.entries(w.nach_art);
  return (
    <div className="ae-wirkung">
      <h5 className="ae-unter">Wirkungsflächen</h5>
      {w.anzahl === 0 && w.neue_pakete === 0 && !w.windows ? (
        <p className="ez-klein">Die Prüfung fand keine neuen Wirkungsflächen.</p>
      ) : (
        <ul className="ae-liste">
          {arten.map(([art, n]) => (
            <li key={art} className="ez-klein">
              {ART_TEXT[art] ?? art}: {n} {n === 1 ? "Fund" : "Funde"}
            </li>
          ))}
          {w.neue_pakete > 0 && <li className="ez-klein">{w.neue_pakete} neue Pakete in den Abhängigkeiten.</li>}
          {w.windows && <li className="ez-klein">Teile für Windows (Host, Desktop) sind nur gebaut, nicht geprüft: Das muss ein Mensch am Laptop prüfen.</li>}
        </ul>
      )}
      {w.sonder_hinweis && <p className="ez-klein">Ein neuer Geheimnisname deutet auf eine Sonderfreigabe hin. Achte darauf, ob die Änderung als Sonderfreigabe eingestuft wurde.</p>}
      {w.treffer.length > 0 && (
        <ul className="ae-liste">
          {w.treffer.map((t, i) => (
            <li key={`${t.p}-${t.z}-${i}`} className="ez-klein num">
              {t.p}:{t.z} ({ART_TEXT[t.a] ?? t.a}) {t.d}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export const AenderungenAbschnitt = memo(function AenderungenAbschnitt() {
  const [status, setStatus] = useState<Status | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [geladen, setGeladen] = useState(false);
  const [laedt, setLaedt] = useState(false);
  const [ausgang, setAusgang] = useState<Ausgang | null>(null);
  const aktiv = useRef(true);

  const laden = useCallback(async () => {
    setLaedt(true);
    const erg = await statusLaden();
    if (!aktiv.current) return;
    if (erg.ok) {
      setStatus(erg.status);
      setFehler(null);
    } else {
      setStatus(null);
      setFehler(erg.text);
    }
    setGeladen(true);
    setLaedt(false);
  }, []);

  useEffect(() => {
    aktiv.current = true;
    const erster = setTimeout(() => void laden(), 0); // nicht im Effekt selbst: Der erste Abruf setzt Zustand
    const takt = setInterval(() => {
      if (document.visibilityState === "visible") void laden();
    }, INTERVALL_MS);
    return () => {
      aktiv.current = false;
      clearTimeout(erster);
      clearInterval(takt);
    };
  }, [laden]);

  const a = useMemo(() => ansicht(status, fehler), [status, fehler]);
  const zuletzt = status?.verfuegbar ? status.abgeschlossen : [];

  return (
    <>
      <Block titel="Lage">
        {!geladen ? (
          <p className="ez-leer" role="status">
            Lade den Stand ...
          </p>
        ) : (
          <div className="ae-lage" data-lage={a.lage} role="status">
            <span className="ae-lage-marke">{LAGE_KENNZEICHEN[a.lage]}</span>
            <p className="ez-text">{a.lageText}</p>
          </div>
        )}
        {a.hinweise.map((h) => (
          <p key={h} className="ez-klein">
            {h}
          </p>
        ))}
        <div className="ae-aktionen">
          <button type="button" className="btn-secondary" onClick={() => void laden()} disabled={laedt}>
            {laedt ? "Lade ..." : "Neu laden"}
          </button>
          {status?.verfuegbar && status.zeit && <span className="ez-klein">Stand vom Kern: {status.zeit}</span>}
        </div>
      </Block>

      {a.anfrage && (
        <Block titel={`Offene Änderung ${a.anfrage.kurz}`}>
          <Daten
            zeilen={[
              { name: "Quelle", wert: a.anfrage.quelleText },
              ...(a.anfrage.versionText ? [{ name: "Version", wert: a.anfrage.versionText }] : []),
              ...(a.anfrage.fristText ? [{ name: "Frist bis", wert: a.anfrage.fristText }] : []),
            ]}
          />
          {a.anfrage.schritte.length > 0 && (
            <ol className="ae-weg" aria-label="Weg der Änderung">
              {a.anfrage.schritte.map((s) => (
                <li key={s.name} data-zustand={s.zustand} aria-current={s.zustand === "aktuell" ? "step" : undefined}>
                  <span className="ae-marke" aria-hidden="true" />
                  <span className="ae-titel">{s.titel}</span>
                  <span className="ae-zustand">{ZUSTAND_TEXT[s.zustand]}</span>
                </li>
              ))}
            </ol>
          )}
          {a.anfrage.pruefstandZeilen.length > 0 && (
            <>
              <h5 className="ae-unter">Prüfstand</h5>
              <Daten zeilen={a.anfrage.pruefstandZeilen} />
            </>
          )}
          {a.anfrage.pruefung?.diff && <DiffTabelle diff={a.anfrage.pruefung.diff} />}
          {a.anfrage.pruefung?.wirkung && <WirkungBlock w={a.anfrage.pruefung.wirkung} />}
          {a.anfrage.versionHinweise.map((h) => (
            <p key={h} className="ez-klein">
              {h}
            </p>
          ))}
          {status?.verfuegbar && status.anfrage && a.wartetAufFreigabe && (
            <AenderungenFreigabe
              anfrage={status.anfrage}
              onAusgang={(x) => {
                setAusgang(x);
                if (x.neuLaden) void laden();
              }}
            />
          )}
          {ausgang && (
            <p className={ausgang.art === "ok" ? "ez-text" : "gv-fehler"} role={ausgang.art === "ok" ? "status" : "alert"}>
              {ausgang.text}
            </p>
          )}
        </Block>
      )}

      {a.zeilen.length > 0 && (
        <Block titel="Dienste">
          <Daten zeilen={a.zeilen} />
        </Block>
      )}

      {zuletzt.length > 0 && (
        <Block titel="Zuletzt abgeschlossen">
          <ul className="ae-zuletzt">
            {zuletzt.map((e) => (
              <li key={e.id} className="ez-klein">
                {abgeschlossenText(e)}
              </li>
            ))}
          </ul>
        </Block>
      )}
    </>
  );
});
