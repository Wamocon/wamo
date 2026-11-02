"use client";

import { memo, useId, useState } from "react";
import { HinweiseListe } from "@/components/Hinweise";
import { useMikrofonListe } from "@/components/MikrofonWahl";
import { useEffektiveDarstellung } from "@/components/kommando/ansicht";
import {
  DARSTELLUNGEN,
  DARSTELLUNG_BESCHREIBUNG,
  DARSTELLUNG_TEXT,
  darstellungSetzen,
} from "@/components/kommando/darstellung";
import { KERN, kernUrl, useBetriebsart } from "@/lib/betriebsart";
import { rolleText } from "@/lib/einstellungenAbschnitte";
import { useDesktopApp, useServerBrowser } from "@/lib/geraet";
import { gewaehltesMikrofon, merkeMikrofon, type Mikrofon } from "@/lib/mikrofon";
import { zurGeraeteverwaltung } from "@/lib/einstellungenHash";
import { useSelect } from "@/lib/store";
import { istOnline, wamo, zeigeToast } from "@/lib/wamo";
import { Block, Daten } from "./Block";

/** Version aus package.json, zur Build-Zeit eingesetzt (next.config.ts), wie in der Fußzeile der Anmeldeseite. */
const VERSION = process.env.WAMO_VERSION ?? "0.1.0";

// ---------- Konto und Profil ----------
export const KontoAbschnitt = memo(function KontoAbschnitt({
  email,
  onAbmelden,
}: {
  email: string | null;
  onAbmelden: () => void;
}) {
  const profil = useSelect(wamo, (s) => s.profil);
  const rolle = profil?.rolle ?? null;
  return (
    <>
      <Block titel="Angemeldet">
        <Daten
          zeilen={[
            { name: "E-Mail", wert: email ?? "unbekannt" },
            { name: "Aktives Profil", wert: profil?.name ?? "erscheint mit dem ersten Gespräch" },
            {
              name: "Rolle",
              wert: rolle ? (
                <span className="role-tag" data-rolle={rolle}>
                  {rolleText(rolle)}
                </span>
              ) : (
                rolleText(null)
              ),
            },
          ]}
        />
        <p className="ez-klein">
          Zum Profilwechsel meldest du dich ab und mit dem anderen Profil wieder an. PC-Werkzeuge, Entwicklung,
          Verbrauch und Änderungen sehen nur Profile mit der Rolle Admin.
        </p>
      </Block>
      <Block titel="Abmelden">
        <p className="ez-text">Beendet ein laufendes Gespräch und meldet dich auf diesem Gerät ab.</p>
        <div>
          <button type="button" className="btn-secondary" onClick={onAbmelden}>
            Abmelden
          </button>
        </div>
      </Block>
    </>
  );
});

// ---------- Sprache und Ton ----------
function MikrofonEinstellung({ laden, waehlen }: { laden: () => Promise<Mikrofon[]>; waehlen: (id: string) => boolean }) {
  const id = useId();
  const liste = useMikrofonListe(laden);
  const online = useSelect(wamo, istOnline);
  const aktuell = useSelect(wamo, (s) => s.mikrofon);
  // Die Wahl liegt in localStorage (lib/mikrofon.ts); hier nur der Wert für die Anzeige
  const [gewaehlt, setGewaehlt] = useState(() => gewaehltesMikrofon() ?? "");
  const wert = liste?.some((m) => m.id === gewaehlt) ? gewaehlt : "";

  return (
    <>
      <div className="ez-feld">
        <label htmlFor={id}>Mikrofon</label>
        <select
          id={id}
          value={wert}
          disabled={!liste || liste.length === 0}
          onChange={(e) => {
            const neu = e.target.value;
            if (neu === "") {
              merkeMikrofon(null);
              setGewaehlt("");
              zeigeToast("Standardgerät gilt ab dem nächsten Gespräch");
            } else if (waehlen(neu)) {
              setGewaehlt(neu);
            }
          }}
        >
          <option value="">Standardgerät von Windows</option>
          {liste?.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </select>
      </div>
      <p className="ez-klein" role="status">
        {!liste
          ? "Mikrofone werden gesucht …"
          : liste.length === 0
            ? "Keine Mikrofone gefunden oder noch keine Freigabe des Browsers. Die Liste erscheint nach dem Start eines Gesprächs."
            : online && aktuell
              ? `In Benutzung: ${aktuell}. Ein Wechsel gilt sofort.`
              : "Gerade läuft kein Gespräch. Die Wahl gilt ab dem nächsten Gespräch auf diesem Gerät."}
      </p>
    </>
  );
}

export const SpracheAbschnitt = memo(function SpracheAbschnitt({
  mikrofone,
  mikrofonWaehlen,
}: {
  mikrofone: () => Promise<Mikrofon[]>;
  mikrofonWaehlen: (id: string) => boolean;
}) {
  const modelle = useSelect(wamo, (s) => s.betrieb?.modelle ?? null);
  return (
    <>
      <Block titel="Eingabe">
        <MikrofonEinstellung laden={mikrofone} waehlen={mikrofonWaehlen} />
      </Block>
      <Block titel="Hören und Sprechen">
        {modelle && (modelle.stt || modelle.tts) ? (
          <Daten
            zeilen={[
              { name: "Spracherkennung", wert: modelle.stt ?? "unbekannt" },
              { name: "Sprachausgabe", wert: modelle.tts ?? "unbekannt" },
            ]}
          />
        ) : (
          <p className="ez-text">
            Spracherkennung und Sprachausgabe deines Profils erscheinen hier, sobald ein Gespräch läuft.
          </p>
        )}
        <p className="ez-klein">Nur zur Anzeige: Die Angaben stammen aus der Meldung des Kerns zu diesem Gespräch.</p>
      </Block>
    </>
  );
});

// ---------- Darstellung ----------
export const DarstellungAbschnitt = memo(function DarstellungAbschnitt() {
  const id = useId();
  const { gewaehlt, pausiert } = useEffektiveDarstellung();
  return (
    <Block titel="Ansicht">
      <fieldset className="ez-radios">
        <legend className="sr-only">Darstellung</legend>
        {DARSTELLUNGEN.map((d) => (
          <label key={d} className="ez-radio" data-an={gewaehlt === d || undefined}>
            <input
              type="radio"
              name={`${id}-darstellung`}
              value={d}
              checked={gewaehlt === d}
              onChange={() => darstellungSetzen(d)}
              aria-labelledby={`${id}-${d}-n`}
              aria-describedby={`${id}-${d}-b`}
            />
            <span className="ez-radio-text">
              <span id={`${id}-${d}-n`} className="ez-radio-name">
                {DARSTELLUNG_TEXT[d]}
              </span>
              <span id={`${id}-${d}-b`} className="ez-radio-beschr">
                {DARSTELLUNG_BESCHREIBUNG[d]}
              </span>
            </span>
          </label>
        ))}
      </fieldset>
      <p className="ez-klein" role="status">
        {pausiert ? `${DARSTELLUNG_TEXT[gewaehlt]} pausiert, solange etwas auf deine Entscheidung wartet.` : ""}
      </p>
      <p className="ez-klein">Die Wahl gilt für dieses Gerät und bleibt erhalten. Im Gespräch wechselst du oben in der Mitte.</p>
    </Block>
  );
});

// ---------- Info ----------
export const InfoAbschnitt = memo(function InfoAbschnitt({ admin }: { admin: boolean }) {
  const gewaehlt = useBetriebsart();
  const betrieb = useSelect(wamo, (s) => s.betrieb);
  const desktop = useDesktopApp();
  const serverBrowser = useServerBrowser();
  // Verbunden: was der Kern meldet. Sonst: die gewählte Betriebsart für das nächste Gespräch.
  const art = betrieb ? (betrieb.modus === "lokal" ? "unterwegs" : "buero") : gewaehlt;
  const adresse = kernUrl(art);
  return (
    <>
      <Block titel="Diese Version">
        <Daten
          zeilen={[
            { name: "Version", wert: <span className="num">{VERSION}</span> },
            { name: "Gerät", wert: desktop ? "Desktop-App" : serverBrowser ? "Browser auf Sokrates-2" : "Browser" },
          ]}
        />
      </Block>
      <Block titel="Kern">
        <Daten
          zeilen={[
            { name: "Betriebsart", wert: `${KERN[art].name}${betrieb ? "" : " (gewählt)"}` },
            { name: "Ort", wert: KERN[art].ort },
            { name: "Adresse", wert: adresse ? <span className="ez-adresse">{adresse}</span> : "nicht eingerichtet" },
          ]}
        />
      </Block>
      {admin && (
        <Block titel="Hinweise für Admins">
          <p className="ez-text">Neue und wartende Geräte sowie weitere Meldungen an dein Profil.</p>
          <div>
            <button type="button" className="btn-secondary btn-small" onClick={zurGeraeteverwaltung}>
              Zur Geräteverwaltung
            </button>
          </div>
          <HinweiseListe />
        </Block>
      )}
    </>
  );
});
