"use client";

import { Fragment, memo, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { TeilenKnopf } from "@/components/Teilen";
import { canvasIndex, canvasWunsch } from "@/lib/canvasWahl";
import { KOPIERT_MS, kopierChipText, type KopierStatus } from "@/lib/kopieren";
import { istUuid } from "@/lib/teilenHilfe";
import { useSelect } from "@/lib/store";
import { useThema } from "@/lib/useThema";
import { wamo } from "@/lib/wamo";

/** Was der Voice-Core per canvas_zeigen schickt (siehe voice-core/tools.py). */
export type CanvasItem = (
  | { id?: string | null; kind: "tabelle"; title: string; columns: string[]; rows: string[][] }
  | { id?: string | null; kind: "code"; title: string; text: string; language?: string }
  | { id?: string | null; kind: "dokument"; title: string; text: string }
  | { id?: string | null; kind: "mermaid"; title: string; text: string }
) & {
  /** gespeichertes Ergebnis eines Entwicklungsauftrags: teilbar als Dokument oder als ganzes Projekt */
  teilbar?: { id: string; projekt?: string };
  /** von jemand anderem geteilt (geöffnet über geteilte Arbeit) */
  geteilt?: { von: string | null; recht: "lesen" | "bearbeiten" };
};

const KIND_LABEL: Record<CanvasItem["kind"], string> = {
  tabelle: "Tabelle",
  code: "Code",
  dokument: "Dokument",
  mermaid: "Diagramm",
};

const BEISPIELE = [
  { name: "Tabellen", hint: "„Zeig mir die Projektliste als Tabelle“" },
  { name: "Dokumente", hint: "„Entwirf ein Angebot für …“" },
  { name: "Diagramme", hint: "„Skizzier die Systemlandschaft von …“" },
  { name: "Code", hint: "„Zeig mir ein SQL-Statement mit JOIN“" },
  { name: "Websuche", hint: "„Wie wird das Wetter morgen?“" },
  { name: "Am PC", hint: "„Öffne WhatsApp“ · „Klick auf Senden“", nurAdmin: true },
];

/**
 * Ergebnisfläche: abonniert nur den eigenen Store-Ausschnitt (canvas) und rendert deshalb nicht
 * neu, wenn sich Untertitel oder Zeitleiste ändern. Leer zeigt sie eine ruhige Beispielliste.
 */
export const CanvasBereich = memo(function CanvasBereich() {
  const items = useSelect(wamo, (s) => s.canvas);
  // PC-Werkzeuge gibt es vorerst nur für admin; Mitgliedern den Hinweis darauf ersparen
  const mitglied = useSelect(wamo, (s) => s.profil?.rolle === "mitglied");
  if (items.length) return <Canvas items={items} />;
  return (
    <div className="canvas-empty">
      <p className="eyebrow">Canvas</p>
      <h2>Was W.A.I.S. dir hier zeigt</h2>
      <p>Ergebnisse, die man sehen muss, landen auf dieser Fläche statt in der Sprache.</p>
      <ul className="kinds">
        {BEISPIELE.filter((k) => !k.nurAdmin || !mitglied).map((k) => (
          <li key={k.name}>
            <strong>{k.name}</strong>
            <span>{k.hint}</span>
          </li>
        ))}
      </ul>
    </div>
  );
});

function Canvas({ items }: { items: CanvasItem[] }) {
  const [picked, setPicked] = useState<number | null>(null);
  // "Ergebnis ansehen" in der Tafel Vorgänge (lib/canvasWahl.ts): ein neuer Wunsch wählt das Element mit dieser Kennung, noch im
  // selben Render (kein Zwischenbild mit dem alten Element). Ein Wunsch, der schon galt, bevor dieses Canvas entstand, zählt nicht.
  const wunsch = useSelect(canvasWunsch, (w) => w);
  const [gesehen, setGesehen] = useState(wunsch.n);
  if (wunsch.n !== gesehen) {
    setGesehen(wunsch.n);
    const i = wunsch.id ? canvasIndex(items, wunsch.id) : -1;
    if (i >= 0) setPicked(i === items.length - 1 ? null : i);
  }
  // Ohne eigene Auswahl zeigt das Canvas immer das Neueste.
  const index = picked !== null && picked < items.length ? picked : items.length - 1;
  const item = items[index];

  return (
    <div className="canvas-board">
      {items.length > 1 && (
        <nav className="canvas-tabs" aria-label="Frühere Inhalte">
          {items.map((it, i) => (
            <button
              type="button"
              key={`${i}-${it.title}`}
              className="canvas-tab"
              aria-current={i === index}
              onClick={() => setPicked(i === items.length - 1 ? null : i)}
            >
              {it.title}
            </button>
          ))}
        </nav>
      )}
      <article className="canvas-item">
        <header className="canvas-item-head">
          <span className="eyebrow">{KIND_LABEL[item.kind]}</span>
          <h2>{item.title}</h2>
          {item.geteilt && (
            <p className="canvas-geteilt">
              Geteilt von {item.geteilt.von ?? "jemandem"}, {item.geteilt.recht === "bearbeiten" ? "du darfst bearbeiten" : "nur lesen"}
            </p>
          )}
          <CanvasTeilen item={item} />
        </header>
        <CanvasBody item={item} />
      </article>
    </div>
  );
}

/** "Teilen" nur für eigene, gespeicherte Elemente (id aus der Datenbank oder gespeichertes Auftragsergebnis). */
function CanvasTeilen({ item }: { item: CanvasItem }) {
  if (item.geteilt) return null; // fremde Arbeit teilt nur ihr Besitzer
  const id = item.teilbar?.id ?? (istUuid(item.id) ? item.id : null);
  if (!id) return null;
  // Ergebnisse von Entwicklungsaufträgen (teilbar) nur zum Lesen
  const ziel = { art: "canvas" as const, id, titel: item.title, projekt: item.teilbar?.projekt, nurLesen: Boolean(item.teilbar) };
  return <TeilenKnopf key={id} ziel={ziel} />;
}

function CanvasBody({ item }: { item: CanvasItem }) {
  switch (item.kind) {
    case "tabelle":
      return (
        <div className="canvas-table-wrap" tabIndex={0} role="region" aria-label={`Tabelle: ${item.title}`}>
          <table className="canvas-table">
            <thead>
              <tr>
                {item.columns.map((c, i) => (
                  <th key={i}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {item.rows.map((row, r) => (
                <tr key={r}>
                  {item.columns.map((_, c) => (
                    <td key={c}>{row[c] ?? ""}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "code":
      return <CodeView text={item.text} language={item.language} title={item.title} />;
    case "dokument":
      return <div className="canvas-doc">{renderMarkdown(item.text)}</div>;
    case "mermaid":
      return (
        <MermaidView
          key={item.id ?? item.title} // je Element eigener Zustand: ein Klick gilt nie für ein anderes Diagramm
          source={item.text}
          title={item.title}
          geteiltVon={item.geteilt ? (item.geteilt.von ?? null) : undefined}
        />
      );
  }
}

function CodeView({ text, language, title }: { text: string; language?: string; title: string }) {
  // Die Knopfbeschriftung bleibt "Kopieren"; die Bestätigung ist ein Chip in einer Live-Region daneben (lib/kopieren.ts)
  const [status, setStatus] = useState<KopierStatus>("");
  const uhr = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (uhr.current) clearTimeout(uhr.current);
    },
    [],
  );
  const kopieren = async () => {
    let neu: KopierStatus = "kopiert";
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      neu = "fehler"; // z. B. Zwischenablage nicht erlaubt
    }
    setStatus(neu);
    if (uhr.current) clearTimeout(uhr.current);
    uhr.current = setTimeout(() => setStatus(""), KOPIERT_MS);
  };
  return (
    <div className="canvas-code">
      <div className="canvas-code-bar">
        <span>{language || "text"}</span>
        <span className="canvas-code-aktion">
          <span className="canvas-chip" aria-live="polite" data-art={status === "fehler" ? "fehler" : undefined}>
            {kopierChipText(status)}
          </span>
          <button type="button" onClick={() => void kopieren()}>
            Kopieren
          </button>
        </span>
      </div>
      <pre tabIndex={0} role="region" aria-label={`Code: ${title}`}>
        <code>{text}</code>
      </pre>
    </div>
  );
}

// Mermaid im WMC-Theme: schwarze Knotenränder, graue Linien, Hinweise mit roter Kante
const MERMAID_HELL = {
  fontFamily: '"Arial Narrow", "Liberation Sans Narrow", "Roboto Condensed", Arial, sans-serif',
  primaryColor: "#FFFFFF",
  primaryTextColor: "#000000",
  primaryBorderColor: "#000000",
  lineColor: "#6B6B6B",
  secondaryColor: "#F3F3F3",
  tertiaryColor: "#FFFFFF",
  clusterBkg: "#F3F3F3",
  clusterBorder: "#D9D9D9",
  edgeLabelBackground: "#FFFFFF",
  noteBkgColor: "#F3F3F3",
  noteBorderColor: "#F40E0E",
  noteTextColor: "#000000",
};
const MERMAID_DUNKEL = {
  ...MERMAID_HELL,
  darkMode: true,
  primaryColor: "#1A1A1A",
  primaryTextColor: "#F3F3F3",
  primaryBorderColor: "#F3F3F3",
  lineColor: "#A3A3A3",
  secondaryColor: "#0E0E0E",
  tertiaryColor: "#1A1A1A",
  clusterBkg: "#1A1A1A",
  clusterBorder: "#2E2E2E",
  edgeLabelBackground: "#0E0E0E",
  noteBkgColor: "#1A1A1A",
  noteTextColor: "#F3F3F3",
};

/**
 * Mermaid-Diagramm als Bild: das SVG landet nie im DOM der Oberfläche (kein innerHTML), sondern als blob:-URL in einem
 * <img> (dort laufen weder Skripte noch Verweise). Von anderen geteilte Diagramme werden erst nach einem Klick
 * gezeichnet.
 */
function MermaidView({ source, title, geteiltVon }: { source: string; title: string; geteiltVon?: string | null }) {
  const id = useId().replace(/[^a-zA-Z0-9]/g, "");
  const thema = useThema(); // ein Wechsel Hell oder Dunkel zeichnet das Diagramm mit den passenden Farben neu
  const [error, setError] = useState<string | null>(null);
  const [freigegeben, setFreigegeben] = useState(geteiltVon === undefined);
  const [bild, setBild] = useState<{ url: string; breite: number | null } | null>(null);

  useEffect(() => {
    if (!freigegeben) return;
    let cancelled = false;
    let url: string | null = null;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        const dark = thema === "dunkel";
        // strict: Mermaid säubert das SVG selbst (kein HTML/Script aus dem Diagrammtext).
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "base",
          themeVariables: dark ? MERMAID_DUNKEL : MERMAID_HELL,
          // keine eigene Fehlergrafik von Mermaid im Dokument; Fehler zeigen wir selbst
          suppressErrorRendering: true,
        });
        const { svg } = await mermaid.render(`m${id}`, source);
        if (cancelled) return;
        const max = /max-width:\s*([\d.]+)px/.exec(svg);
        url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
        setBild({ url, breite: max ? Number(max[1]) : null });
        setError(null);
      } catch (e) {
        if (cancelled) return;
        setBild(null);
        setError(e instanceof Error ? e.message.slice(0, 300) : "Diagramm konnte nicht gezeichnet werden.");
      }
    })();
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [source, id, freigegeben, thema]);

  if (!freigegeben) {
    return (
      <div className="canvas-mermaid-wartet">
        <p className="notice">Diagramm von {geteiltVon ?? "jemand anderem"}: wird erst nach deinem Klick gezeichnet.</p>
        <button type="button" className="btn-secondary btn-small" onClick={() => setFreigegeben(true)}>
          Diagramm anzeigen
        </button>
      </div>
    );
  }
  return (
    <>
      <div className="canvas-mermaid" tabIndex={0} role="region" aria-label={`Diagramm: ${title}`}>
        {bild && (
          // eslint-disable-next-line @next/next/no-img-element -- lokale blob:-URL, next/image kann sie nicht optimieren
          <img
            src={bild.url}
            alt={`Diagramm: ${title}`}
            style={{ width: "100%", maxWidth: bild.breite ? `${bild.breite}px` : undefined }}
          />
        )}
      </div>
      {error && (
        <>
          <p className="notice">Diagramm fehlerhaft: {error}</p>
          <CodeView text={source} language="mermaid" title={title} />
        </>
      )}
    </>
  );
}

/** Kleines, sicheres Markdown: #-Ueberschriften, Listen, Absaetze, **fett**, `code`. Kein HTML. */
function renderMarkdown(text: string): ReactNode {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flushList = () => {
    if (list.length) {
      blocks.push(
        <ul key={`l${blocks.length}`}>
          {list.map((li, i) => (
            <li key={i}>{inline(li)}</li>
          ))}
        </ul>,
      );
      list = [];
    }
  };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    if (bullet) {
      list.push(bullet[1]);
      continue;
    }
    flushList();
    if (heading) {
      const level = heading[1].length;
      const Tag = (["h3", "h4", "h5"] as const)[level - 1];
      blocks.push(<Tag key={`h${blocks.length}`}>{inline(heading[2])}</Tag>);
    } else if (line.trim()) {
      blocks.push(<p key={`p${blocks.length}`}>{inline(line)}</p>);
    }
  }
  flushList();
  return blocks;
}

function inline(text: string): ReactNode {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}
