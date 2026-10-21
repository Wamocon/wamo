"use client";

import { Fragment, memo, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { TeilenKnopf } from "@/components/Teilen";
import { istUuid } from "@/lib/kernApi";
import { useSelect } from "@/lib/store";
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
      <h2>Was WAMO dir hier zeigt</h2>
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
  return <TeilenKnopf key={id} ziel={{ art: "canvas", id, titel: item.title, projekt: item.teilbar?.projekt }} />;
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
      return <MermaidView source={item.text} title={item.title} />;
  }
}

function CodeView({ text, language, title }: { text: string; language?: string; title: string }) {
  const [status, setStatus] = useState<"" | "kopiert" | "fehler">("");
  const kopieren = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setStatus("kopiert");
    } catch {
      setStatus("fehler"); // z. B. Zwischenablage nicht erlaubt
    }
    setTimeout(() => setStatus(""), 1500);
  };
  return (
    <div className="canvas-code">
      <div className="canvas-code-bar">
        <span>{language || "text"}</span>
        <button type="button" onClick={() => void kopieren()}>
          {status === "kopiert" ? "Kopiert" : status === "fehler" ? "Nicht kopiert" : "Kopieren"}
        </button>
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

function MermaidView({ source, title }: { source: string; title: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useId().replace(/[^a-zA-Z0-9]/g, "");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        const theme = document.documentElement.dataset.theme;
        const dark = theme ? theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
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
        if (!cancelled && ref.current) {
          ref.current.innerHTML = svg;
          setError(null);
        }
      } catch (e) {
        if (cancelled) return;
        ref.current?.replaceChildren(); // halbfertige Reste entfernen
        setError(e instanceof Error ? e.message.slice(0, 300) : "Diagramm konnte nicht gezeichnet werden.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [source, id]);

  return (
    <>
      <div className="canvas-mermaid" tabIndex={0} role="region" aria-label={`Diagramm: ${title}`}>
        <div ref={ref} role="img" aria-label={`Diagramm: ${title}`} />
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
