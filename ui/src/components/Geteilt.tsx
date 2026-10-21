"use client";

import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { TeilenKnopf } from "@/components/Teilen";
import {
  groesseText,
  KernFehler,
  kernDateiHolen,
  kernDateiSenden,
  kernJson,
  type DateiEintrag,
  type Recht,
  type Teilung,
} from "@/lib/kernApi";
import { parseServerMessage } from "@/lib/nachrichten";
import { applyServerMessage, zeigeToast } from "@/lib/wamo";

/**
 * "Geteilte Arbeit": was andere mit mir geteilt haben (öffnen, herunterladen, bearbeiten, zurückgeben), was ich geteilt
 * habe (Recht ändern, Protokoll, widerrufen) und meine Dateien auf Sokrates-2 (hochladen, teilen, löschen).
 * Alles läuft über den Kern (voice-core/teilen_api.py); Titel, Namen und Inhalte anderer Personen sind reiner Text.
 * Geöffnete Dokumente landen auf dem Canvas (dieselbe Prüfung wie Nachrichten des Kerns, lib/nachrichten.ts).
 */

type Reiter = "mit_mir" | "von_mir" | "dateien";
type Liste = { von_mir: Teilung[]; mit_mir: Teilung[] };
type CanvasAntwort = {
  item: Record<string, unknown> & { kind?: string; title?: string; text?: string; language?: string };
  recht: "besitzer" | Recht;
  besitzer: string | null;
};
type Ereignis = { ereignis: string; at: string | null; durch: string | null };

const ART_TEXT: Record<string, string> = { canvas: "Dokument", auftrag: "Projekt", datei: "Datei" };
const EREIGNIS_TEXT: Record<string, string> = {
  geteilt: "geteilt",
  recht_geaendert: "Recht geändert",
  widerrufen: "widerrufen",
  zurueckgegeben: "zurückgegeben",
  abgerufen: "angesehen",
  bearbeitet: "bearbeitet",
  geloescht: "gelöscht",
};
const TEXT_ARTEN = new Set(["dokument", "code", "mermaid"]);

function zeit(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
}

function fehlerText(e: unknown, standard: string): string {
  return e instanceof KernFehler ? e.message : standard;
}

/** Aufs Canvas legen, geprüft wie jede Nachricht des Kerns. */
function aufsCanvas(antwort: CanvasAntwort) {
  const geteilt = antwort.recht === "besitzer" ? undefined : { von: antwort.besitzer, recht: antwort.recht };
  const msg = parseServerMessage({ type: "canvas", item: { ...antwort.item, geteilt } });
  if (msg) applyServerMessage(msg);
  else zeigeToast("Dieses Dokument lässt sich nicht anzeigen");
}

/** Zweistufiger Knopf für endgültige Schritte (widerrufen, löschen, zurückgeben). */
function SicherKnopf({ text, frage, onJa }: { text: string; frage: string; onJa: () => Promise<void> | void }) {
  const [fragt, setFragt] = useState(false);
  useEffect(() => {
    if (!fragt) return;
    const uhr = setTimeout(() => setFragt(false), 6000);
    return () => clearTimeout(uhr);
  }, [fragt]);
  return (
    <button
      type="button"
      className="btn-secondary btn-small geteilt-gefahr"
      data-fragt={fragt || undefined}
      onClick={() => {
        if (!fragt) setFragt(true);
        else {
          setFragt(false);
          void onJa();
        }
      }}
    >
      {fragt ? frage : text}
    </button>
  );
}

export function GeteiltBereich() {
  const [reiter, setReiter] = useState<Reiter>("mit_mir");
  const [liste, setListe] = useState<Liste | null>(null);
  const [dateien, setDateien] = useState<DateiEintrag[] | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [laedt, setLaedt] = useState(false);
  const id = useId();

  const laden = useCallback(async () => {
    setLaedt(true);
    setFehler(null);
    try {
      const [l, d] = await Promise.all([
        kernJson<Liste>("/teilen"),
        kernJson<{ dateien: DateiEintrag[] }>("/dateien"),
      ]);
      setListe(l);
      setDateien(d.dateien);
    } catch (e) {
      setFehler(fehlerText(e, "Geteilte Arbeit gerade nicht verfügbar"));
    } finally {
      setLaedt(false);
    }
  }, []);

  const reiterListe: { key: Reiter; text: string; anzahl?: number }[] = [
    { key: "mit_mir", text: "Mit mir geteilt", anzahl: liste?.mit_mir.length },
    { key: "von_mir", text: "Von mir geteilt", anzahl: liste?.von_mir.length },
    { key: "dateien", text: "Meine Dateien", anzahl: dateien?.length },
  ];

  return (
    <details
      className="geteilt"
      onToggle={(e) => {
        if (e.currentTarget.open) void laden(); // beim Aufklappen frisch laden
      }}
    >
      <summary>Geteilte Arbeit</summary>
      <div className="geteilt-reiter" role="tablist" aria-label="Geteilte Arbeit">
        {reiterListe.map((r) => (
          <button
            key={r.key}
            type="button"
            role="tab"
            id={`${id}-${r.key}`}
            aria-selected={reiter === r.key}
            aria-controls={`${id}-panel`}
            data-an={reiter === r.key || undefined}
            onClick={() => setReiter(r.key)}
          >
            {r.text}
            {r.anzahl !== undefined && ` (${r.anzahl})`}
          </button>
        ))}
        <button type="button" className="geteilt-neu" onClick={() => void laden()} disabled={laedt}>
          {laedt ? "lädt …" : "Aktualisieren"}
        </button>
      </div>
      <div role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-${reiter}`} className="geteilt-panel">
        {fehler && <p className="notice">{fehler}</p>}
        {reiter === "mit_mir" && liste && <MitMir teilungen={liste.mit_mir} onAenderung={laden} />}
        {reiter === "von_mir" && liste && <VonMir teilungen={liste.von_mir} onAenderung={laden} />}
        {reiter === "dateien" && dateien && <MeineDateien dateien={dateien} onAenderung={laden} />}
      </div>
    </details>
  );
}

function Leer({ children }: { children: ReactNode }) {
  return <p className="geteilt-leer">{children}</p>;
}

function MitMir({ teilungen, onAenderung }: { teilungen: Teilung[]; onAenderung: () => Promise<void> }) {
  const [bearbeiten, setBearbeiten] = useState<{ id: string; antwort: CanvasAntwort } | null>(null);
  if (!teilungen.length) return <Leer>Noch hat niemand etwas mit dir geteilt.</Leer>;

  const oeffnen = async (canvasId: string, alsEditor: boolean) => {
    try {
      const antwort = await kernJson<CanvasAntwort>(`/geteilt/canvas/${encodeURIComponent(canvasId)}`);
      aufsCanvas(antwort);
      if (alsEditor) setBearbeiten({ id: canvasId, antwort });
    } catch (e) {
      zeigeToast(fehlerText(e, "Öffnen fehlgeschlagen"));
    }
  };

  const projektOeffnen = async (t: Teilung) => {
    try {
      const { dokumente } = await kernJson<{ dokumente: { id: string }[] }>(
        `/teilen/${encodeURIComponent(t.id)}/dokumente`,
      );
      if (!dokumente.length) zeigeToast("Zu diesem Projekt gibt es noch keine Ergebnisse");
      else await oeffnen(dokumente[0].id, false);
    } catch (e) {
      zeigeToast(fehlerText(e, "Öffnen fehlgeschlagen"));
    }
  };

  return (
    <>
      <ul className="geteilt-liste">
        {teilungen.map((t) => (
          <li key={t.id} className="geteilt-eintrag">
            <p className="geteilt-titel">{t.titel}</p>
            <p className="geteilt-meta">
              {ART_TEXT[t.art] ?? t.art} von {t.name}, {t.recht === "bearbeiten" ? "lesen und bearbeiten" : "lesen"}
              {t.erstellt_at && `, seit ${zeit(t.erstellt_at)}`}
            </p>
            {t.hinweis && <p className="geteilt-hinweis">„{t.hinweis}“</p>}
            <div className="geteilt-aktionen">
              {t.art === "canvas" && (
                <button type="button" className="btn-secondary btn-small" onClick={() => void oeffnen(t.objekt, false)}>
                  Öffnen
                </button>
              )}
              {t.art === "canvas" && t.recht === "bearbeiten" && (
                <button type="button" className="btn-secondary btn-small" onClick={() => void oeffnen(t.objekt, true)}>
                  Bearbeiten
                </button>
              )}
              {t.art === "auftrag" && (
                <button type="button" className="btn-secondary btn-small" onClick={() => void projektOeffnen(t)}>
                  Neuestes Ergebnis öffnen
                </button>
              )}
              {t.art === "datei" && (
                <button
                  type="button"
                  className="btn-secondary btn-small"
                  onClick={() =>
                    void kernDateiHolen(`/dateien/${encodeURIComponent(t.objekt)}`, t.titel).catch((e: unknown) =>
                      zeigeToast(fehlerText(e, "Herunterladen fehlgeschlagen")),
                    )
                  }
                >
                  Herunterladen
                </button>
              )}
              {t.art === "datei" && t.recht === "bearbeiten" && (
                <FassungHochladen dateiId={t.objekt} onFertig={onAenderung} />
              )}
              <SicherKnopf
                text="Zurückgeben"
                frage="Wirklich zurückgeben?"
                onJa={async () => {
                  try {
                    await kernJson(`/teilen/${encodeURIComponent(t.id)}/zurueckgeben`, "POST");
                    await onAenderung();
                  } catch (e) {
                    zeigeToast(fehlerText(e, "Zurückgeben fehlgeschlagen"));
                  }
                }}
              />
            </div>
          </li>
        ))}
      </ul>
      {bearbeiten && (
        <Editor
          canvasId={bearbeiten.id}
          antwort={bearbeiten.antwort}
          onSchliessen={() => setBearbeiten(null)}
          onGespeichert={() => void oeffnen(bearbeiten.id, false)}
        />
      )}
    </>
  );
}

function Editor({
  canvasId,
  antwort,
  onSchliessen,
  onGespeichert,
}: {
  canvasId: string;
  antwort: CanvasAntwort;
  onSchliessen: () => void;
  onGespeichert: () => void;
}) {
  const id = useId();
  const kind = String(antwort.item.kind ?? "");
  const [titel, setTitel] = useState(String(antwort.item.title ?? ""));
  const [text, setText] = useState(String(antwort.item.text ?? ""));
  const [laeuft, setLaeuft] = useState(false);
  const kopf = useRef<HTMLHeadingElement>(null);
  useEffect(() => kopf.current?.focus(), []);

  if (!TEXT_ARTEN.has(kind)) {
    return (
      <div className="geteilt-editor">
        <p className="notice">Tabellen lassen sich hier nicht bearbeiten.</p>
        <button type="button" className="error-close" onClick={onSchliessen}>
          Schließen
        </button>
      </div>
    );
  }

  const speichern = async () => {
    setLaeuft(true);
    try {
      const inhalt: Record<string, string> = { text };
      if (kind === "code" && typeof antwort.item.language === "string") inhalt.language = antwort.item.language;
      await kernJson(`/geteilt/canvas/${encodeURIComponent(canvasId)}`, "PUT", { titel, inhalt });
      zeigeToast("Neue Fassung gespeichert");
      onGespeichert();
      onSchliessen();
    } catch (e) {
      zeigeToast(fehlerText(e, "Speichern fehlgeschlagen"));
    } finally {
      setLaeuft(false);
    }
  };

  return (
    <form
      className="geteilt-editor"
      onSubmit={(e) => {
        e.preventDefault();
        void speichern();
      }}
    >
      <h3 ref={kopf} tabIndex={-1}>
        Bearbeiten (neue Fassung)
      </h3>
      <label htmlFor={`${id}-titel`}>Titel</label>
      <input id={`${id}-titel`} type="text" maxLength={300} value={titel} onChange={(e) => setTitel(e.target.value)} />
      <label htmlFor={`${id}-text`}>Inhalt</label>
      <textarea id={`${id}-text`} rows={14} value={text} onChange={(e) => setText(e.target.value)} />
      <div className="teilen-aktionen">
        <button type="submit" className="btn-secondary btn-small" disabled={laeuft || !titel.trim() || !text.trim()}>
          {laeuft ? "Speichert …" : "Als neue Fassung speichern"}
        </button>
        <button type="button" className="error-close" onClick={onSchliessen}>
          Abbrechen
        </button>
      </div>
    </form>
  );
}

function FassungHochladen({ dateiId, onFertig }: { dateiId: string; onFertig: () => Promise<void> }) {
  const eingabe = useRef<HTMLInputElement>(null);
  const [laeuft, setLaeuft] = useState(false);
  return (
    <>
      <input
        ref={eingabe}
        type="file"
        hidden
        onChange={async (e) => {
          const datei = e.target.files?.[0];
          e.target.value = "";
          if (!datei) return;
          setLaeuft(true);
          try {
            await kernDateiSenden(`/dateien/${encodeURIComponent(dateiId)}/fassung`, datei);
            zeigeToast("Neue Fassung abgelegt");
            await onFertig();
          } catch (err) {
            zeigeToast(fehlerText(err, "Hochladen fehlgeschlagen"));
          } finally {
            setLaeuft(false);
          }
        }}
      />
      <button
        type="button"
        className="btn-secondary btn-small"
        disabled={laeuft}
        onClick={() => eingabe.current?.click()}
      >
        {laeuft ? "Lädt hoch …" : "Neue Fassung hochladen"}
      </button>
    </>
  );
}

function VonMir({ teilungen, onAenderung }: { teilungen: Teilung[]; onAenderung: () => Promise<void> }) {
  const [protokoll, setProtokoll] = useState<{ id: string; ereignisse: Ereignis[] } | null>(null);
  if (!teilungen.length) return <Leer>Du hast noch nichts geteilt. Teilen geht am Canvas und unter Meine Dateien.</Leer>;

  const rechtSetzen = async (t: Teilung, recht: Recht) => {
    try {
      await kernJson(`/teilen/${encodeURIComponent(t.id)}/recht`, "POST", { recht });
      await onAenderung();
    } catch (e) {
      zeigeToast(fehlerText(e, "Recht nicht geändert"));
    }
  };

  const zeigeProtokoll = async (t: Teilung) => {
    if (protokoll?.id === t.id) {
      setProtokoll(null);
      return;
    }
    try {
      const { ereignisse } = await kernJson<{ ereignisse: Ereignis[] }>(`/teilen/${encodeURIComponent(t.id)}/protokoll`);
      setProtokoll({ id: t.id, ereignisse });
    } catch (e) {
      zeigeToast(fehlerText(e, "Protokoll nicht verfügbar"));
    }
  };

  return (
    <ul className="geteilt-liste">
      {teilungen.map((t) => (
        <li key={t.id} className="geteilt-eintrag">
          <p className="geteilt-titel">{t.titel}</p>
          <p className="geteilt-meta">
            {ART_TEXT[t.art] ?? t.art} mit {t.name}
            {t.erstellt_at && `, seit ${zeit(t.erstellt_at)}`}
          </p>
          <div className="geteilt-aktionen">
            <label className="geteilt-recht">
              <span>Recht</span>
              <select
                value={t.recht}
                disabled={t.art === "auftrag"}
                onChange={(e) => void rechtSetzen(t, e.target.value as Recht)}
              >
                <option value="lesen">lesen</option>
                <option value="bearbeiten">lesen und bearbeiten</option>
              </select>
            </label>
            <button
              type="button"
              className="btn-secondary btn-small"
              aria-expanded={protokoll?.id === t.id}
              onClick={() => void zeigeProtokoll(t)}
            >
              Protokoll
            </button>
            <SicherKnopf
              text="Widerrufen"
              frage="Wirklich widerrufen?"
              onJa={async () => {
                try {
                  await kernJson(`/teilen/${encodeURIComponent(t.id)}/widerrufen`, "POST");
                  zeigeToast(`Für ${t.name} nicht mehr geteilt`);
                  await onAenderung();
                } catch (e) {
                  zeigeToast(fehlerText(e, "Widerrufen fehlgeschlagen"));
                }
              }}
            />
          </div>
          {protokoll?.id === t.id && (
            <ol className="geteilt-protokoll">
              {protokoll.ereignisse.map((e, i) => (
                <li key={i}>
                  {zeit(e.at)}: {EREIGNIS_TEXT[e.ereignis] ?? e.ereignis}
                  {e.durch && ` (${e.durch})`}
                </li>
              ))}
            </ol>
          )}
        </li>
      ))}
    </ul>
  );
}

function MeineDateien({ dateien, onAenderung }: { dateien: DateiEintrag[]; onAenderung: () => Promise<void> }) {
  const eingabe = useRef<HTMLInputElement>(null);
  const [laeuft, setLaeuft] = useState(false);

  const hochladen = async (datei: File) => {
    setLaeuft(true);
    try {
      await kernDateiSenden("/dateien", datei);
      zeigeToast(`${datei.name} liegt jetzt auf Sokrates-2`);
      await onAenderung();
    } catch (e) {
      zeigeToast(fehlerText(e, "Hochladen fehlgeschlagen"));
    } finally {
      setLaeuft(false);
    }
  };

  return (
    <>
      <div className="geteilt-upload">
        <input
          ref={eingabe}
          type="file"
          hidden
          accept=".pdf,.png,.jpg,.jpeg,.gif,.webp,.txt,.csv,.md,.json,.zip,.docx,.xlsx,.pptx,.odt,.ods,.odp"
          onChange={(e) => {
            const datei = e.target.files?.[0];
            e.target.value = "";
            if (datei) void hochladen(datei);
          }}
        />
        <button type="button" className="btn-secondary btn-small" disabled={laeuft} onClick={() => eingabe.current?.click()}>
          {laeuft ? "Lädt hoch …" : "Datei hochladen"}
        </button>
        <span className="pc-switch-hint">
          Wird nach Sokrates-2 kopiert (höchstens 25 MB, PDF, Bilder, Text, Office). Danach hier teilen.
        </span>
      </div>
      {!dateien.length ? (
        <Leer>Noch keine Dateien.</Leer>
      ) : (
        <ul className="geteilt-liste">
          {dateien.map((d) => (
            <li key={d.id} className="geteilt-eintrag">
              <p className="geteilt-titel">{d.name}</p>
              <p className="geteilt-meta">
                {groesseText(d.groesse)}
                {d.fassungen > 1 && `, ${d.fassungen} Fassungen`}
                {d.herkunft === "scan" && ", Scan"}
                {d.erstellt_at && `, ${zeit(d.erstellt_at)}`}
              </p>
              <div className="geteilt-aktionen">
                <TeilenKnopf ziel={{ art: "datei", id: d.id, titel: d.name }} />
                <button
                  type="button"
                  className="btn-secondary btn-small"
                  onClick={() =>
                    void kernDateiHolen(`/dateien/${encodeURIComponent(d.id)}`, d.name).catch((e: unknown) =>
                      zeigeToast(fehlerText(e, "Herunterladen fehlgeschlagen")),
                    )
                  }
                >
                  Herunterladen
                </button>
                <SicherKnopf
                  text="Löschen"
                  frage="Wirklich löschen?"
                  onJa={async () => {
                    try {
                      await kernJson(`/dateien/${encodeURIComponent(d.id)}/loeschen`, "POST");
                      await onAenderung();
                    } catch (e) {
                      zeigeToast(fehlerText(e, "Löschen fehlgeschlagen"));
                    }
                  }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
