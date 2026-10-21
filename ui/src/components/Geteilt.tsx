"use client";

import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { TeilenKnopf } from "@/components/Teilen";
import {
  alsCanvasAntwort,
  alsDateien,
  alsEreignisse,
  alsTeilungen,
  DATEI_ACCEPT,
  groesseText,
  KernFehler,
  kernDateiHolen,
  kernDateiSenden,
  kernJson,
  type CanvasAntwort,
  type DateiEintrag,
  type Ereignis,
  type Recht,
  type Teilung,
} from "@/lib/kernApi";
import { parseServerMessage } from "@/lib/nachrichten";
import { applyServerMessage, zeigeToast } from "@/lib/wamo";

/**
 * "Geteilte Arbeit": was andere mit mir geteilt haben (öffnen, herunterladen, bearbeiten, zurückgeben), was ich geteilt
 * habe (Recht ändern, Protokoll, widerrufen) und meine Dateien auf Sokrates-2 (hochladen, teilen, löschen).
 * Alles läuft über den Kern (voice-core/teilen_api.py); Antworten werden geprüft (lib/teilenHilfe.ts), Titel, Namen und
 * Inhalte anderer Personen sind reiner Text. Geöffnete Dokumente landen auf dem Canvas (dieselbe Prüfung wie
 * Nachrichten des Kerns, lib/nachrichten.ts). Die drei Bereiche bleiben beim Umschalten erhalten (offener Editor).
 */

type Bereich = "mit_mir" | "von_mir" | "dateien";
type Daten = { von_mir: Teilung[]; mit_mir: Teilung[]; dateien: DateiEintrag[] };

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

async function canvasLaden(canvasId: string): Promise<CanvasAntwort> {
  const antwort = alsCanvasAntwort(await kernJson(`/geteilt/canvas/${encodeURIComponent(canvasId)}`));
  if (!antwort) throw new KernFehler(0, "Unerwartete Antwort des Kerns");
  return antwort;
}

/** Zweistufiger Knopf für endgültige Schritte (widerrufen, löschen, zurückgeben); die Rückfrage wird angesagt. */
function SicherKnopf({
  text,
  frage,
  label,
  onJa,
}: {
  text: string;
  frage: string;
  label: string;
  onJa: () => Promise<void> | void;
}) {
  const [fragt, setFragt] = useState(false);
  const [laeuft, setLaeuft] = useState(false);
  useEffect(() => {
    if (!fragt) return;
    const uhr = setTimeout(() => setFragt(false), 15_000);
    return () => clearTimeout(uhr);
  }, [fragt]);
  return (
    <>
      <button
        type="button"
        className="btn-secondary btn-small geteilt-gefahr"
        data-fragt={fragt || undefined}
        aria-label={fragt ? `${frage} ${label}` : `${text}: ${label}`}
        disabled={laeuft}
        onClick={async () => {
          if (!fragt) {
            setFragt(true);
            return;
          }
          setFragt(false);
          setLaeuft(true);
          try {
            await onJa();
          } finally {
            setLaeuft(false);
          }
        }}
      >
        {fragt ? frage : text}
      </button>
      <span className="sr-only" aria-live="polite">
        {fragt ? `${frage} Zum Bestätigen noch einmal drücken.` : ""}
      </span>
    </>
  );
}

/** Knopf mit Busy-Zustand für Downloads. */
function LadeKnopf({ label, onKlick, children }: { label: string; onKlick: () => Promise<void>; children: ReactNode }) {
  const [laeuft, setLaeuft] = useState(false);
  return (
    <button
      type="button"
      className="btn-secondary btn-small"
      aria-label={label}
      aria-busy={laeuft}
      disabled={laeuft}
      onClick={async () => {
        setLaeuft(true);
        try {
          await onKlick();
        } finally {
          setLaeuft(false);
        }
      }}
    >
      {laeuft ? "lädt …" : children}
    </button>
  );
}

export function GeteiltBereich() {
  const [bereich, setBereich] = useState<Bereich>("mit_mir");
  const [daten, setDaten] = useState<Daten | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);
  const [laedt, setLaedt] = useState(false);
  const runde = useRef(0); // gegen Überholen: nur die Antwort der letzten Anfrage zählt
  const id = useId();

  const laden = useCallback(async () => {
    const meine = ++runde.current;
    setLaedt(true);
    const [teilungen, dateien] = await Promise.allSettled([kernJson("/teilen"), kernJson("/dateien")]);
    if (meine !== runde.current) return;
    setLaedt(false);
    const t = teilungen.status === "fulfilled" ? (teilungen.value as { von_mir?: unknown; mit_mir?: unknown }) : null;
    const d = dateien.status === "fulfilled" ? (dateien.value as { dateien?: unknown }) : null;
    setDaten((alt) => ({
      von_mir: t ? alsTeilungen(t.von_mir) : (alt?.von_mir ?? []),
      mit_mir: t ? alsTeilungen(t.mit_mir) : (alt?.mit_mir ?? []),
      dateien: d ? alsDateien(d.dateien) : (alt?.dateien ?? []),
    }));
    const grund = [teilungen, dateien].find((r) => r.status === "rejected") as PromiseRejectedResult | undefined;
    setFehler(grund ? fehlerText(grund.reason, "Geteilte Arbeit gerade nicht verfügbar") : null);
  }, []);

  const bereiche: { key: Bereich; text: string; anzahl?: number }[] = [
    { key: "mit_mir", text: "Mit mir geteilt", anzahl: daten?.mit_mir.length },
    { key: "von_mir", text: "Von mir geteilt", anzahl: daten?.von_mir.length },
    { key: "dateien", text: "Meine Dateien", anzahl: daten?.dateien.length },
  ];

  return (
    <details
      className="geteilt"
      onToggle={(e) => {
        if (e.currentTarget.open) void laden(); // beim Aufklappen frisch laden
      }}
    >
      <summary>Geteilte Arbeit</summary>
      <div className="geteilt-reiter" role="group" aria-label="Bereich wählen">
        {bereiche.map((b) => (
          <button
            key={b.key}
            type="button"
            aria-pressed={bereich === b.key}
            aria-controls={`${id}-${b.key}`}
            onClick={() => setBereich(b.key)}
          >
            {b.text}
            {b.anzahl !== undefined && ` (${b.anzahl})`}
          </button>
        ))}
        <button type="button" className="geteilt-neu" onClick={() => void laden()} disabled={laedt}>
          {laedt ? "lädt …" : "Aktualisieren"}
        </button>
      </div>
      {fehler && (
        <p className="notice" role="alert">
          {fehler}
        </p>
      )}
      <div className="geteilt-panel" aria-busy={laedt}>
        <section id={`${id}-mit_mir`} hidden={bereich !== "mit_mir"} aria-label="Mit mir geteilt">
          {daten && <MitMir teilungen={daten.mit_mir} onAenderung={laden} />}
        </section>
        <section id={`${id}-von_mir`} hidden={bereich !== "von_mir"} aria-label="Von mir geteilt">
          {daten && <VonMir teilungen={daten.von_mir} onAenderung={laden} />}
        </section>
        <section id={`${id}-dateien`} hidden={bereich !== "dateien"} aria-label="Meine Dateien">
          {daten && <MeineDateien dateien={daten.dateien} onAenderung={laden} />}
        </section>
      </div>
    </details>
  );
}

function Leer({ children }: { children: ReactNode }) {
  return <p className="geteilt-leer">{children}</p>;
}

type Bearbeitung = { canvasId: string; antwort: CanvasAntwort; ausloeser: HTMLElement | null };

function MitMir({ teilungen, onAenderung }: { teilungen: Teilung[]; onAenderung: () => Promise<void> }) {
  const [bearbeiten, setBearbeiten] = useState<Bearbeitung | null>(null);

  const oeffnen = async (canvasId: string, alsEditor: HTMLElement | null) => {
    try {
      const antwort = await canvasLaden(canvasId);
      aufsCanvas(antwort);
      if (alsEditor) setBearbeiten({ canvasId, antwort, ausloeser: alsEditor });
    } catch (e) {
      zeigeToast(fehlerText(e, "Öffnen fehlgeschlagen"));
    }
  };

  const projektOeffnen = async (t: Teilung) => {
    try {
      const d = (await kernJson(`/teilen/${encodeURIComponent(t.id)}/dokumente`)) as { dokumente?: unknown };
      const erstes = Array.isArray(d?.dokumente) ? (d.dokumente[0] as { id?: unknown } | undefined) : undefined;
      if (typeof erstes?.id !== "string") zeigeToast("Zu diesem Projekt gibt es noch keine Ergebnisse");
      else await oeffnen(erstes.id, null);
    } catch (e) {
      zeigeToast(fehlerText(e, "Öffnen fehlgeschlagen"));
    }
  };

  const editorSchliessen = () => {
    const ausloeser = bearbeiten?.ausloeser;
    setBearbeiten(null);
    requestAnimationFrame(() => ausloeser?.focus());
  };

  if (!teilungen.length && !bearbeiten) return <Leer>Noch hat niemand etwas mit dir geteilt.</Leer>;
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
                <LadeKnopf label={`Öffnen: ${t.titel}`} onKlick={() => oeffnen(t.objekt, null)}>
                  Öffnen
                </LadeKnopf>
              )}
              {t.art === "canvas" && t.recht === "bearbeiten" && (
                <button
                  type="button"
                  className="btn-secondary btn-small"
                  aria-label={`Bearbeiten: ${t.titel}`}
                  onClick={(e) => void oeffnen(t.objekt, e.currentTarget)}
                >
                  Bearbeiten
                </button>
              )}
              {t.art === "auftrag" && (
                <LadeKnopf label={`Neuestes Ergebnis öffnen: ${t.titel}`} onKlick={() => projektOeffnen(t)}>
                  Neuestes Ergebnis öffnen
                </LadeKnopf>
              )}
              {t.art === "datei" && (
                <LadeKnopf
                  label={`Herunterladen: ${t.titel}`}
                  onKlick={() =>
                    kernDateiHolen(`/dateien/${encodeURIComponent(t.objekt)}`, t.titel).catch((e: unknown) =>
                      zeigeToast(fehlerText(e, "Herunterladen fehlgeschlagen")),
                    )
                  }
                >
                  Herunterladen
                </LadeKnopf>
              )}
              {t.art === "datei" && t.recht === "bearbeiten" && (
                <FassungHochladen dateiId={t.objekt} titel={t.titel} onFertig={onAenderung} />
              )}
              <SicherKnopf
                text="Zurückgeben"
                frage="Wirklich zurückgeben?"
                label={t.titel}
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
          key={bearbeiten.canvasId}
          canvasId={bearbeiten.canvasId}
          antwort={bearbeiten.antwort}
          onSchliessen={editorSchliessen}
          onGespeichert={() => void oeffnen(bearbeiten.canvasId, null)}
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
  const kind = typeof antwort.item.kind === "string" ? antwort.item.kind : "";
  const titel = typeof antwort.item.title === "string" ? antwort.item.title : "Dokument";
  const [basis, setBasis] = useState(antwort.version);
  const [text, setText] = useState(typeof antwort.item.text === "string" ? antwort.item.text : "");
  const [laeuft, setLaeuft] = useState(false);
  const [meldung, setMeldung] = useState<{ text: string; konflikt: boolean } | null>(null);
  const kopf = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    kopf.current?.focus();
  }, []);

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
    setMeldung(null);
    try {
      const inhalt: Record<string, string> = { text };
      if (kind === "code" && typeof antwort.item.language === "string") inhalt.language = antwort.item.language;
      await kernJson(`/geteilt/canvas/${encodeURIComponent(canvasId)}`, "PUT", { inhalt, basis });
      zeigeToast("Neue Fassung gespeichert");
      onGespeichert();
      onSchliessen();
    } catch (e) {
      const konflikt = e instanceof KernFehler && e.status === 409;
      setMeldung({ text: fehlerText(e, "Speichern fehlgeschlagen"), konflikt });
    } finally {
      setLaeuft(false);
    }
  };

  // Konflikt: neueste Fassung zeigen; der eigene Text bleibt im Feld, damit nichts verloren geht
  const neuLaden = async () => {
    try {
      const neu = await canvasLaden(canvasId);
      aufsCanvas(neu);
      setBasis(neu.version);
      setMeldung({ text: `Fassung ${neu.version} liegt jetzt auf dem Canvas. Dein Text ist noch hier.`, konflikt: false });
    } catch (e) {
      setMeldung({ text: fehlerText(e, "Neu laden fehlgeschlagen"), konflikt: true });
    }
  };

  return (
    <form
      className="geteilt-editor"
      aria-busy={laeuft}
      onSubmit={(e) => {
        e.preventDefault();
        void speichern();
      }}
    >
      <h3 ref={kopf} tabIndex={-1}>
        {titel} bearbeiten (neue Fassung)
      </h3>
      <label htmlFor={`${id}-text`}>Inhalt</label>
      <textarea id={`${id}-text`} rows={14} maxLength={100_000} value={text} onChange={(e) => setText(e.target.value)} />
      {meldung && (
        <p className="teilen-status" role="alert" data-fehler={meldung.konflikt || undefined}>
          {meldung.text}
        </p>
      )}
      <div className="teilen-aktionen">
        <button type="submit" className="btn-secondary btn-small" disabled={laeuft || !text.trim()}>
          {laeuft ? "Speichert …" : "Als neue Fassung speichern"}
        </button>
        {meldung?.konflikt && (
          <button type="button" className="btn-secondary btn-small" onClick={() => void neuLaden()}>
            Neueste Fassung laden
          </button>
        )}
        <button type="button" className="error-close" onClick={onSchliessen}>
          Abbrechen
        </button>
      </div>
    </form>
  );
}

function FassungHochladen({ dateiId, titel, onFertig }: { dateiId: string; titel: string; onFertig: () => Promise<void> }) {
  const eingabe = useRef<HTMLInputElement>(null);
  const [laeuft, setLaeuft] = useState(false);
  return (
    <>
      <input
        ref={eingabe}
        type="file"
        hidden
        accept={DATEI_ACCEPT}
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
        aria-label={`Neue Fassung hochladen: ${titel}`}
        aria-busy={laeuft}
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
      const d = (await kernJson(`/teilen/${encodeURIComponent(t.id)}/protokoll`)) as { ereignisse?: unknown };
      setProtokoll({ id: t.id, ereignisse: alsEreignisse(d?.ereignisse) });
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
                aria-label={`Recht für ${t.name}: ${t.titel}`}
                disabled={t.art === "auftrag"}
                onChange={(e) => void rechtSetzen(t, e.target.value === "bearbeiten" ? "bearbeiten" : "lesen")}
              >
                <option value="lesen">lesen</option>
                <option value="bearbeiten">lesen und bearbeiten</option>
              </select>
            </label>
            <button
              type="button"
              className="btn-secondary btn-small"
              aria-expanded={protokoll?.id === t.id}
              aria-label={`Protokoll: ${t.titel} mit ${t.name}`}
              onClick={() => void zeigeProtokoll(t)}
            >
              Protokoll
            </button>
            <SicherKnopf
              text="Widerrufen"
              frage="Wirklich widerrufen?"
              label={`${t.titel} für ${t.name}`}
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
              {protokoll.ereignisse.length === 0 && <li>Noch keine Einträge</li>}
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
          accept={DATEI_ACCEPT}
          onChange={(e) => {
            const datei = e.target.files?.[0];
            e.target.value = "";
            if (datei) void hochladen(datei);
          }}
        />
        <button
          type="button"
          className="btn-secondary btn-small"
          aria-busy={laeuft}
          disabled={laeuft}
          onClick={() => eingabe.current?.click()}
        >
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
                <LadeKnopf
                  label={`Herunterladen: ${d.name}`}
                  onKlick={() =>
                    kernDateiHolen(`/dateien/${encodeURIComponent(d.id)}`, d.name).catch((e: unknown) =>
                      zeigeToast(fehlerText(e, "Herunterladen fehlgeschlagen")),
                    )
                  }
                >
                  Herunterladen
                </LadeKnopf>
                <SicherKnopf
                  text="Löschen"
                  frage="Wirklich löschen?"
                  label={d.name}
                  onJa={async () => {
                    try {
                      await kernJson(`/dateien/${encodeURIComponent(d.id)}/loeschen`, "POST");
                      zeigeToast(`${d.name} gelöscht; geteilte Zugriffe enden sofort`);
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
