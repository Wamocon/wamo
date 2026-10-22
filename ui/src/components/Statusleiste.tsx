"use client";

import { memo } from "react";
import { KERN, useBetriebsart, type Betriebsart } from "@/lib/betriebsart";
import type { Betrieb } from "@/lib/nachrichten";
import { useSelect } from "@/lib/store";
import { unverschluesselteDienste } from "@/lib/umgebung";
import { istOnline, wamo } from "@/lib/wamo";

// Build-Konstanten: auf Server und Client gleich
const UNSICHER = unverschluesselteDienste().map((d) => d.name);

// Was der Kern meldet (lokal = Kern auf diesem PC, also Betriebsart Unterwegs)
const MODUS_ART: Record<Betrieb["modus"], Betriebsart> = { lokal: "unterwegs", buero: "buero" };

function Symbol({ art }: { art: "haus" | "laptop" | "schloss" | "warnung" }) {
  // schlichte Liniensymbole, Farbe über currentColor; das Warnsymbol ist ein rotes Quadrat
  if (art === "warnung") {
    return (
      <svg className="sym sym-warn" viewBox="0 0 16 16" aria-hidden="true">
        <rect x="1" y="1" width="14" height="14" rx="2" />
        <path d="M8 4v5M8 11.2v.6" />
      </svg>
    );
  }
  return (
    <svg className="sym" viewBox="0 0 16 16" aria-hidden="true">
      {art === "haus" && <path d="M2.5 14V5.5L8 2l5.5 3.5V14M6 14v-4h4v4M1.5 14h13" />}
      {art === "laptop" && <path d="M3 4h10v7H3zM1 13h14" />}
      {art === "schloss" && <path d="M4 7.5h8V14H4zM5.8 7.5V5.2a2.2 2.2 0 0 1 4.4 0v2.3" />}
    </svg>
  );
}

/** PC-Host: Kurzform "PC" mit Punkt oder Symbol, die Langform steht in .status-label. */
function PcText({ betrieb }: { betrieb: Betrieb | null }) {
  if (!betrieb) {
    return (
      <>
        <span className="dot" aria-hidden="true" />
        PC<span className="status-label"> unbekannt</span>
      </>
    );
  }
  switch (betrieb.pc) {
    case "verbindet":
      return (
        <>
          <span className="dot" aria-hidden="true" />
          PC<span className="status-label"> verbindet …</span>
        </>
      );
    case "verbunden":
      return (
        <>
          <span className="dot" data-on="true" aria-hidden="true" />
          PC
          <span className="status-label">
            {betrieb.pcName && <span className="num"> {betrieb.pcName}</span>} verbunden
            {betrieb.pcLatenzMs !== undefined && <span className="num latency"> {betrieb.pcLatenzMs} ms</span>}
          </span>
        </>
      );
    case "getrennt":
      return (
        <>
          <Symbol art="warnung" />
          <strong>
            PC<span className="status-label"> nicht erreichbar</span>
          </strong>
        </>
      );
    case "gesperrt":
      return (
        <>
          <Symbol art="schloss" />
          PC<span className="status-label">: Bildschirm gesperrt</span>
        </>
      );
    case "aus":
      return (
        <>
          <span className="dot" aria-hidden="true" />
          PC<span className="status-label">-Steuerung aus</span>
        </>
      );
  }
}

/**
 * Status als Beschreibungsliste: Verbindung, Betriebsart, Modelle, PC-Host, Mikrofon.
 * "kompakt" für die Kopfzeile (Bezeichnungen nur für Screenreader), sonst ausführlich (Menü).
 * Ohne Meldung "betrieb" vom Kern steht "unbekannt".
 */
export const StatusDetails = memo(function StatusDetails({ kompakt }: { kompakt: boolean }) {
  const presence = useSelect(wamo, (s) => s.presence);
  const online = useSelect(wamo, istOnline);
  const betrieb = useSelect(wamo, (s) => s.betrieb);
  const micOn = useSelect(wamo, (s) => s.micOn);
  const mikrofon = useSelect(wamo, (s) => s.mikrofon);
  const gewaehlt = useBetriebsart();
  const dt = kompakt ? "sr-only" : undefined;
  // Verbunden: was der Kern meldet. Sonst: die gewählte Betriebsart für das nächste Gespräch.
  const art = betrieb ? MODUS_ART[betrieb.modus] : gewaehlt;
  const kernText = `Kern: ${KERN[art].ort}`;
  const m = betrieb?.modelle;
  const modelleKurz = (m && [m.llm, m.stt].filter(Boolean).join(" · ")) || "Modelle unbekannt";

  return (
    <dl className={kompakt ? "status-items" : "status-list"}>
      <div data-feld="verbindung">
        <dt className={dt}>Verbindung</dt>
        <dd>
          <span className="dot" data-on={online || undefined} aria-hidden="true" />
          <span className="status-label">
            {online ? "verbunden" : presence === "connecting" ? "verbindet …" : "getrennt"}
          </span>
        </dd>
      </div>
      <div data-feld="betriebsart">
        <dt className={dt}>Betriebsart</dt>
        <dd title={kernText}>
          <Symbol art={art === "buero" ? "haus" : "laptop"} />
          {KERN[art].name}
          {!kompakt && <span className="status-label"> ({kernText}{betrieb ? "" : ", gewählt"})</span>}
        </dd>
      </div>
      {kompakt ? (
        <div data-feld="modelle">
          <dt className={dt}>Modelle</dt>
          <dd title={m ? `Gespräch: ${m.llm ?? "unbekannt"}, Hören: ${m.stt ?? "unbekannt"}, Stimme: ${m.tts ?? "unbekannt"}` : undefined}>
            {modelleKurz}
            {betrieb?.ort && <span className="tag">{betrieb.ort === "cloud" ? "Cloud" : "im Haus"}</span>}
          </dd>
        </div>
      ) : (
        <>
          <div data-feld="modelle">
            <dt>Gespräch</dt>
            <dd>
              {m?.llm ?? "unbekannt"}
              {betrieb?.ort && <span className="tag">{betrieb.ort === "cloud" ? "Cloud" : "im Haus"}</span>}
            </dd>
          </div>
          <div>
            <dt>Hören</dt>
            <dd>{m?.stt ?? "unbekannt"}</dd>
          </div>
          <div>
            <dt>Stimme</dt>
            <dd>{m?.tts ?? "unbekannt"}</dd>
          </div>
        </>
      )}
      {betrieb?.pcSteuerung === "an" && (
        <div data-feld="pcsteuerung">
          <dt className={dt}>PC-Steuerung</dt>
          <dd>
            <span className="dot dot-rot" aria-hidden="true" />
            <strong>
              PC-Steuerung<span className="status-label"> an</span>
            </strong>
          </dd>
        </div>
      )}
      {!kompakt && betrieb && betrieb.pcSteuerung !== "an" && (
        <div>
          <dt>PC-Steuerung</dt>
          <dd>aus</dd>
        </div>
      )}
      {betrieb?.entwicklung === "aus" && (
        <div data-feld="entwicklung">
          <dt className={dt}>Entwicklung</dt>
          <dd>{kompakt ? "Entwicklung gesperrt" : "gesperrt"}</dd>
        </div>
      )}
      <div data-feld="pc" data-pc={betrieb?.pc}>
        <dt className={dt}>PC-Host</dt>
        <dd>
          <PcText betrieb={betrieb} />
        </dd>
      </div>
      {UNSICHER.length > 0 && (
        <div data-feld="netz">
          <dt className={dt}>Netz</dt>
          <dd title={`Unverschlüsselt im LAN: ${UNSICHER.join(", ")}`}>
            <span className="tag tag-warn">unverschlüsselt</span>
            {!kompakt && <span> im LAN: {UNSICHER.join(", ")}</span>}
          </dd>
        </div>
      )}
      <div data-feld="mikro">
        <dt className={dt}>Mikrofon</dt>
        <dd title={online && mikrofon ? `Gerät: ${mikrofon}` : undefined}>
          {!online ? "Mikro bereit" : micOn ? "Mikro an" : <strong>Mikro aus</strong>}
          {!kompakt && online && mikrofon && <span className="status-label"> ({mikrofon})</span>}
        </dd>
      </div>
    </dl>
  );
});
