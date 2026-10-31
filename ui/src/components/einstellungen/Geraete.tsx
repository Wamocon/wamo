"use client";

import { memo, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { StatusDetails } from "@/components/Statusleiste";
import {
  BETRIEBSARTEN,
  BUERO_VERFUEGBAR,
  betriebsartSetzen,
  KERN,
  useBetriebsart,
  type Betriebsart,
} from "@/lib/betriebsart";
import { pcAutoSetzen, usePcAuto } from "@/lib/einstellungen";
import { geraeteBloecke, geraeteStandText } from "@/lib/einstellungenAbschnitte";
import { useDesktopApp, useServerBrowser } from "@/lib/geraet";
import { nachbar } from "@/lib/modellwahl";
import { geraeteAnzeige } from "@/lib/geraeteAnzeige";
import { useSelect } from "@/lib/store";
import { istOnline, wamo, zeigeToast } from "@/lib/wamo";
import { Block } from "./Block";
import { GeraeteVerwaltung } from "./GeraeteVerwaltung";

/** So lange wartet der Schalter auf die Rückmeldung des Kerns, danach ist er wieder bedienbar. */
const SCHALT_WARTEN_MS = 5000;

/**
 * Schalter "PC-Steuerung" für diese Sitzung: Standard aus, nur für admin und nur bei Verbindung.
 * Gesendet wird nur der Wunsch; was gilt, meldet der Kern in betrieb.pc_steuerung zurück.
 */
const PcSteuerungSchalter = memo(function PcSteuerungSchalter({ onSchalten }: { onSchalten: (an: boolean) => boolean }) {
  const admin = useSelect(wamo, (s) => s.profil?.rolle === "admin");
  const online = useSelect(wamo, istOnline);
  const an = useSelect(wamo, (s) => s.betrieb?.pcSteuerung === "an");
  const belegt = useSelect(wamo, (s) => s.betrieb?.pcBelegt === true && s.betrieb?.pcSteuerung !== "an");
  const pcName = useSelect(wamo, (s) => s.betrieb?.pcName ?? null);
  const [ziel, setZiel] = useState<boolean | null>(null);
  const auto = usePcAuto();
  const serverBrowser = useServerBrowser();
  const ausstehend = ziel !== null && ziel !== an && !belegt;
  if (!admin) return null;
  if (serverBrowser) {
    // Browser auf dem Server: die PC-Werkzeuge gehören zur Desktop-App (lib/geraet.ts)
    return (
      <div className="pc-steuerung">
        <p className="pc-switch-hint">PC-Steuerung: nur in der Desktop-App verfügbar</p>
      </div>
    );
  }
  const hinweis = ausstehend
    ? "wird geschaltet …"
    : belegt
      ? "eine andere Sitzung steuert diesen PC gerade"
      : online
        ? `gilt nur für diese Sitzung${pcName ? `, Ziel ${pcName}` : ""}`
        : "Der Schalter ist während eines Gesprächs bedienbar.";
  return (
    <div className="pc-steuerung">
      <div className="pc-switch-row">
        <PcSchalterKnopf an={an} ausstehend={ausstehend} gesperrt={!online} onSchalten={onSchalten} setZiel={setZiel} />
        <span className="pc-switch-hint">{hinweis}</span>
      </div>
      <label className="pc-auto">
        <input type="checkbox" checked={auto} onChange={(e) => pcAutoSetzen(e.target.checked)} />
        <span>PC-Steuerung beim Start automatisch einschalten (dieses Gerät)</span>
      </label>
      {auto && <p className="pc-auto-hint">Ohne Sprecherprüfung reagiert WAMO dann auf jede Stimme im Raum.</p>}
    </div>
  );
});

function PcSchalterKnopf({
  an,
  ausstehend,
  gesperrt,
  onSchalten,
  setZiel,
}: {
  an: boolean;
  ausstehend: boolean;
  gesperrt: boolean;
  onSchalten: (an: boolean) => boolean;
  setZiel: (ziel: boolean | null) => void;
}) {
  const uhr = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (uhr.current) clearTimeout(uhr.current);
  }, []);
  return (
    <button
      type="button"
      role="switch"
      aria-checked={an}
      className="pc-switch"
      data-an={an || undefined}
      disabled={ausstehend || gesperrt}
      onClick={() => {
        const neu = !an;
        if (!onSchalten(neu)) return;
        setZiel(neu);
        if (uhr.current) clearTimeout(uhr.current);
        uhr.current = setTimeout(() => setZiel(null), SCHALT_WARTEN_MS); // ohne Rückmeldung wieder bedienbar
      }}
    >
      <span className="pc-switch-bahn" aria-hidden="true">
        <span className="pc-switch-knopf" />
      </span>
      {/* feste Beschriftung, der Zustand steht in aria-checked; der sichtbare Zusatz wird nicht doppelt vorgelesen */}
      <span>PC-Steuerung</span>
      <span aria-hidden="true">{an ? "an" : "aus"}</span>
    </button>
  );
}

/**
 * Betriebsart dieses Geräts: Büro (Kern auf Sokrates-2) oder Unterwegs (Kern auf diesem PC). Umschalten nur ohne
 * laufendes Gespräch; es gilt ab dem nächsten Verbinden. In der Desktop-App startet "Unterwegs" den lokalen Kern.
 */
const BetriebsartWahl = memo(function BetriebsartWahl() {
  const art = useBetriebsart();
  const offline = useSelect(wamo, (s) => s.presence === "offline");
  const [laeuft, setLaeuft] = useState(false);
  const serverBrowser = useServerBrowser();
  const knoepfe = useRef<(HTMLButtonElement | null)[]>([]);
  if (!BUERO_VERFUEGBAR || serverBrowser) return null; // Browser auf dem Server: nur Büro, nichts zu wählen
  const waehle = (neu: Betriebsart) => {
    if (neu === art || !offline || laeuft) return;
    setLaeuft(true);
    void betriebsartSetzen(neu)
      .then((gilt) => zeigeToast(`Betriebsart ${KERN[gilt].name}: Kern ${KERN[gilt].ort}`))
      .catch(() => zeigeToast("Betriebsart konnte nicht umgeschaltet werden"))
      .finally(() => setLaeuft(false));
  };
  // Radiogruppe nach WAI-ARIA wie ModellWahl: ein Tabstopp, Pfeiltasten wählen, Home und Ende springen
  const taste = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const ziel = nachbar(index, e.key, BETRIEBSARTEN.length);
    if (ziel === null) return;
    e.preventDefault();
    knoepfe.current[ziel]?.focus();
    waehle(BETRIEBSARTEN[ziel]);
  };
  return (
    <div className="betriebsart">
      <div className="betriebsart-wahl" role="radiogroup" aria-label="Betriebsart">
        {BETRIEBSARTEN.map((a, i) => (
          <button
            key={a}
            ref={(el) => {
              knoepfe.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={a === art}
            data-an={a === art || undefined}
            tabIndex={a === art ? 0 : -1}
            disabled={!offline || laeuft}
            onClick={() => waehle(a)}
            onKeyDown={(e) => taste(e, i)}
          >
            {KERN[a].name}
          </button>
        ))}
      </div>
      <span className="pc-switch-hint">
        Kern: {KERN[art].ort}
        {!offline && " (umschalten nach dem Gespräch)"}
      </span>
    </div>
  );
});

/** Stand der Einrichtung dieses Laptops (Desktop-App, Betriebsart Büro); gelesen aus dem Store von GeraetEinrichtung. */
const EinrichtungStand = memo(function EinrichtungStand() {
  const stand = useSelect(geraeteAnzeige, (s) => s);
  const art = useBetriebsart();
  const desktop = useDesktopApp();
  const { text, art: zustand } = geraeteStandText(stand, { desktop, bueroBetrieb: art === "buero" });
  return (
    <p className="ez-text ez-stand" data-zustand={zustand} role="status">
      {text}
    </p>
  );
});

/**
 * Abschnitt "Geräte und PC": Status der Verbindungen (jeder), Geräteverwaltung und PC-Steuerung samt Autostart (nur Admin), Betriebsart
 * (wo es etwas zu wählen gibt) und der Stand der Geräteeinrichtung (nur Desktop-App). Welche Blöcke es gibt, entscheidet
 * geraeteBloecke (lib/einstellungenAbschnitte.ts).
 */
export const GeraeteAbschnitt = memo(function GeraeteAbschnitt({ onPcSteuerung }: { onPcSteuerung: (an: boolean) => boolean }) {
  const rolle = useSelect(wamo, (s) => s.profil?.rolle ?? null);
  const desktop = useDesktopApp();
  const serverBrowser = useServerBrowser();
  const bloecke = geraeteBloecke({ rolle, desktop, serverBrowser, bueroVerfuegbar: BUERO_VERFUEGBAR });
  return (
    <>
      {bloecke.includes("status") && (
        <Block titel="Verbindungen">
          <StatusDetails kompakt={false} />
        </Block>
      )}
      {bloecke.includes("verwaltung") && (
        <Block titel="Geräteverwaltung">
          <GeraeteVerwaltung />
        </Block>
      )}
      {bloecke.includes("pc") && (
        <Block titel="PC-Steuerung">
          <PcSteuerungSchalter onSchalten={onPcSteuerung} />
        </Block>
      )}
      {bloecke.includes("betriebsart") && (
        <Block titel="Betriebsart">
          <BetriebsartWahl />
        </Block>
      )}
      {bloecke.includes("einrichtung") && (
        <Block titel="Gerät einrichten">
          <EinrichtungStand />
        </Block>
      )}
    </>
  );
});
