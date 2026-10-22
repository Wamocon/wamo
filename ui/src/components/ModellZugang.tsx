"use client";

import { memo, useEffect, useRef, useState } from "react";
import { ModellWahl } from "@/components/ModellWahl";
import { useModellzugangSenden } from "@/lib/modellzugangApi";
import { ANTWORT_MS } from "@/lib/modellzugangHilfe";
import type { Modellzugang } from "@/lib/nachrichten";
import {
  API_ANLEITUNG,
  falschesFeld,
  NICHT_AUTOMATISCH,
  TOKEN_ANLEITUNG,
  type Anleitung,
} from "@/lib/schluesselhilfe";
import { useSelect } from "@/lib/store";
import type { ModellzugangAnfrage, SchluesselArt } from "@/lib/useWamo";
import { istOnline, wamo } from "@/lib/wamo";

// API-Schlüssel (sk-ant-api…) und Claude-Code-Token (sk-ant-oat…) sind verschiedene Dinge: jedes Feld nimmt nur seins
const API_MUSTER = /^sk-ant-(?!oat)[A-Za-z0-9_-]{20,250}$/;
const TOKEN_MUSTER = /^sk-ant-oat\d{2}-[A-Za-z0-9_-]{20,400}$/;
const WORKSPACE_MUSTER = /^wrkspc_[A-Za-z0-9]{6,60}$/;

function kopfzeile(z: Modellzugang | null): string {
  if (!z?.art) return z?.fehler ? "Stand nicht lesbar" : "Stand wird geladen …";
  if (z.art === "haus") {
    if (z.festgelegt) return "Nur Hausmodelle (vom Admin festgelegt)";
    return z.quelle === "wahl" ? "Nur Hausmodelle (von dir gewählt)" : "Nur Hausmodelle";
  }
  return z.quelle === "zuordnung" ? "Claude über den Schlüssel des Kerns" : "Claude mit eigenem Schlüssel";
}

function zeitText(iso?: string, wort = "am"): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : `, ${wort} ${d.toLocaleDateString("de-DE")} um ${d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} Uhr`;
}

type Hinweis = { art: SchluesselArt; text: string; fehler: boolean };
type Wartet = { art: SchluesselArt; aktion: "hinterlegen" | "loeschen" };

type FormularProps = {
  art: SchluesselArt;
  /** Name des Blocks ("Gespräch", "Entwicklungsaufträge"); Überschrift der Stufe 4 */
  ueberschrift: string;
  /** wofür der Schlüssel gilt, ein Satz unter der Überschrift */
  zweck: string;
  /** was der Kern daraus macht (zum Beispiel "wird im Modus Nur Hausmodelle nicht benutzt"), optional */
  wirkung?: string;
  anleitung: Anleitung;
  beschriftung: string;
  platzhalter: string;
  hinterlegt: boolean;
  ende?: string;
  gesetzt?: string;
  wartet: Wartet | null;
  hinweis: Hinweis | null;
  workspace?: boolean;
  /** true: gesendet (das Feld wird geleert); false: abgelehnt (falsches Format), der Wert bleibt zum Korrigieren */
  onHinterlegen: (wert: string, workspace: string) => boolean;
  /** der Wert gehört in das andere Feld: dort hinterlegen (der Wert wandert nie durch Meldungen) */
  onUmleiten: (art: SchluesselArt, wert: string) => boolean;
  onLoeschen: () => void;
};

/**
 * Ein Feld für einen Schlüsseltyp. Der Wert geht einmal an den Kern und bleibt nicht in der Oberfläche: das Feld
 * wird beim Absenden geleert, angezeigt werden nur "hinterlegt" und die letzten vier Zeichen.
 */
function SchluesselFormular(p: FormularProps) {
  const [wert, setWert] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [loeschenFragen, setLoeschenFragen] = useState(false);
  const meins = p.wartet?.art === p.art;
  // Es läuft eine Anfrage (irgendeines Schlüsseltyps): Eingaben und Knöpfe ruhen, behalten aber den Fokus
  const beschaeftigt = p.wartet !== null;
  const idKopf = `schluessel-${p.art}`;
  const hinweis = p.hinweis?.art === p.art ? p.hinweis : null;
  const anleitungRef = useRef<HTMLDetailsElement>(null);
  const eingabeRef = useRef<HTMLInputElement>(null);
  const loeschenRef = useRef<HTMLButtonElement>(null);
  const wirklichRef = useRef<HTMLButtonElement>(null);
  const warFrage = useRef(false);
  // Fokus bei der Rückfrage zum Löschen: auf "Wirklich löschen", nach "Abbrechen" zurück auf "Löschen"
  useEffect(() => {
    if (loeschenFragen) wirklichRef.current?.focus();
    else if (warFrage.current) loeschenRef.current?.focus();
    warFrage.current = loeschenFragen;
  }, [loeschenFragen]);
  // Steht ein Wert im falschen Feld (sk-ant-oat… im API-Feld oder umgekehrt), sagt die Karte klar, wohin er gehört
  const falsch = falschesFeld(p.art, wert);
  // "Fertig, eintragen": Anleitung einklappen und ins Feld springen
  const fertig = () => {
    if (anleitungRef.current) anleitungRef.current.open = false;
    eingabeRef.current?.focus();
  };

  return (
    <section className="mz-abschnitt" aria-labelledby={idKopf} aria-busy={meins || undefined}>
      <h4 id={idKopf} className="mz-ueberschrift">
        {p.ueberschrift}
      </h4>
      <p className="mz-zweck">{p.zweck}</p>
      <p className="mz-status" data-hinterlegt={p.hinterlegt || undefined}>
        <span className="mz-marke" aria-hidden="true" />
        {p.hinterlegt ? (
          <span>
            <strong>Hinterlegt</strong>, endet auf …{p.ende ?? "????"}
            {zeitText(p.gesetzt, p.art === "api" ? "geprüft am" : "gespeichert am")}.
          </span>
        ) : (
          <span>
            <strong>Nicht hinterlegt.</strong>
          </span>
        )}
      </p>
      {p.wirkung && <p className="mz-text">{p.wirkung}</p>}
      {/* Geführte Anleitung: offen, solange noch nichts hinterlegt ist; key setzt sie beim Wechsel zurück */}
      <details key={String(p.hinterlegt)} ref={anleitungRef} className="mz-mehr" open={!p.hinterlegt}>
        <summary>{p.anleitung.ueberschrift}</summary>
        <ol className="mz-schritte">
          {p.anleitung.schritte.map((s) => (
            <li key={s.text}>
              {s.text}
              {s.link && (
                <>
                  {" "}
                  <a href={s.link.url} target="_blank" rel="noopener noreferrer">
                    {s.link.wort} (neuer Tab)
                  </a>
                </>
              )}
              {s.befehl && (
                <>
                  {" "}
                  <code>{s.befehl}</code>
                </>
              )}
            </li>
          ))}
          <li>
            {p.anleitung.zweck} {NICHT_AUTOMATISCH}
          </li>
        </ol>
        <button type="button" className="btn-secondary btn-small" onClick={fertig}>
          Fertig, eintragen
        </button>
      </details>
      <form
        className="mz-form"
        autoComplete="off"
        onSubmit={(e) => {
          e.preventDefault();
          if (beschaeftigt || !wert.trim() || falsch) return;
          // Der Wert bleibt nicht in der Oberfläche; wurde er nicht gesendet (falsches Format), bleibt er zum Korrigieren
          if (p.onHinterlegen(wert.trim(), workspace.trim())) setWert("");
        }}
      >
        <label className="mz-feld">
          <span>{p.hinterlegt ? `Neu: ${p.beschriftung}` : p.beschriftung}</span>
          <input
            ref={eingabeRef}
            type="password"
            name={`wamo-${p.art === "api" ? "anthropic" : "claudecode"}`}
            value={wert}
            onChange={(e) => setWert(e.target.value)}
            autoComplete="new-password"
            spellCheck={false}
            autoCapitalize="off"
            data-1p-ignore
            data-lpignore="true"
            placeholder={p.platzhalter}
            readOnly={beschaeftigt}
            aria-invalid={falsch ? true : undefined}
            aria-describedby={falsch ? `${idKopf}-falsch` : undefined}
          />
        </label>
        {falsch && (
          <div id={`${idKopf}-falsch`} className="mz-falsch" role="alert">
            <p className="mz-meldung" data-fehler>
              Fehler: {falsch.text}
            </p>
            <button
              type="button"
              className="btn-secondary btn-small"
              aria-disabled={beschaeftigt || undefined}
              onClick={() => {
                if (beschaeftigt) return;
                // Der Wert verlässt dieses Feld und steht in keiner Meldung; ging er nicht weiter, bleibt er hier
                if (p.onUmleiten(falsch.richtig, wert.trim())) setWert("");
              }}
            >
              {falsch.knopf}
            </button>
          </div>
        )}
        {p.workspace && (
          <details className="mz-mehr">
            <summary>Organisationsschlüssel mit Workspace</summary>
            <label className="mz-feld">
              <span>Workspace-ID (nur wenn nötig)</span>
              <input
                type="text"
                name="wamo-workspace"
                value={workspace}
                onChange={(e) => setWorkspace(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                placeholder="wrkspc_…"
                readOnly={beschaeftigt}
              />
            </label>
          </details>
        )}
        <div className="mz-knoepfe">
          {/* Während des Wartens aria-disabled statt disabled: der Fokus bleibt auf dem Knopf, der Klick wird ignoriert */}
          <button
            type="submit"
            className="btn-primary btn-small"
            aria-disabled={beschaeftigt || !wert.trim() || falsch !== null || undefined}
          >
            {meins && p.wartet?.aktion === "hinterlegen"
              ? p.art === "api"
                ? "wird geprüft …"
                : "wird gespeichert …"
              : p.hinterlegt
                ? "Ersetzen"
                : "Hinterlegen"}
          </button>
          {p.hinterlegt &&
            (loeschenFragen ? (
              <>
                <button
                  ref={wirklichRef}
                  type="button"
                  className="btn-secondary btn-small"
                  aria-disabled={beschaeftigt || undefined}
                  onClick={() => {
                    if (beschaeftigt) return;
                    setLoeschenFragen(false);
                    p.onLoeschen();
                  }}
                >
                  Wirklich löschen
                </button>
                <button type="button" className="btn-secondary btn-small" onClick={() => setLoeschenFragen(false)}>
                  Abbrechen
                </button>
              </>
            ) : (
              <button
                ref={loeschenRef}
                type="button"
                className="btn-secondary btn-small"
                aria-disabled={beschaeftigt || undefined}
                onClick={() => {
                  if (!beschaeftigt) setLoeschenFragen(true);
                }}
              >
                {meins && p.wartet?.aktion === "loeschen" ? "wird gelöscht …" : "Löschen"}
              </button>
            ))}
        </div>
      </form>
      {/* Erfolg höflich (status), Fehler sofort (alert); die status-Region bleibt immer im DOM */}
      <p className="mz-meldung" role="status">
        {hinweis && !hinweis.fehler ? hinweis.text : ""}
      </p>
      {hinweis?.fehler && (
        <p className="mz-meldung" role="alert" data-fehler>
          Fehler: {hinweis.text}
        </p>
      )}
    </section>
  );
}

/**
 * Karte Modellzugang des Profils (eigene Karte in der linken Spalte, auch ohne laufendes Gespräch). Oben der
 * Umschalter Automatisch oder Nur Hausmodelle (ModellWahl), darunter zwei getrennte Schlüsseltypen: der
 * Anthropic-API-Schlüssel für das Gespräch (bezahlt nach Verbrauch, Standardmodell Haiku) und der Claude-Code-Token
 * für Entwicklungsaufträge (aus dem Max-Tarif). Mit Sitzung gehen die Anfragen über den Datenkanal, sonst über die
 * HTTP-Schnittstelle des Kerns (lib/modellzugangApi.ts); der Kern speichert den Schlüssel verschlüsselt. Der
 * API-Schlüssel wird vorher mit einer kleinen Anfrage geprüft, der Token nur auf sein Format (eine Anfrage würde das
 * Kontingent verbrauchen). Nichts kommt zurück: es erscheinen nur "hinterlegt" und die letzten vier Zeichen.
 */
export const ModellZugang = memo(function ModellZugang({
  onAnfrage,
}: {
  onAnfrage?: (anfrage: ModellzugangAnfrage) => boolean;
}) {
  const online = useSelect(wamo, istOnline);
  const zugang = useSelect(wamo, (s) => s.modellzugang);
  const sende = useModellzugangSenden(onAnfrage);
  const [wartet, setWartet] = useState<Wartet | null>(null);
  const [hinweis, setHinweis] = useState<Hinweis | null>(null);
  const uhr = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Antwort des Kerns beendet das Warten (die Wahl des Modus bearbeitet ModellWahl). Abgeleiteter Zustand während des
  // Renderns statt in einem Effekt: React empfiehlt das für "auf eine neue Antwort reagieren". Nur die Antwort auf
  // die eigene Aktion zählt (eine späte Antwort auf die Statusabfrage beendet nichts).
  const [gesehen, setGesehen] = useState(zugang);
  if (zugang !== gesehen) {
    setGesehen(zugang);
    if (zugang && wartet && zugang.aktion === wartet.aktion) {
      setWartet(null);
      setHinweis({
        art: wartet.art,
        text: zugang.fehler
          ? zugang.fehler
          : wartet.aktion === "loeschen"
            ? wartet.art === "api"
              ? "Schlüssel gelöscht."
              : "Token gelöscht."
            : wartet.art === "api"
              ? "Schlüssel geprüft und hinterlegt."
              : "Token gespeichert.",
        fehler: Boolean(zugang.fehler),
      });
    }
  }

  // Die Zeitgrenze fällt weg, sobald nicht mehr gewartet wird (und beim Schließen der Karte)
  useEffect(() => {
    if (wartet === null && uhr.current) {
      clearTimeout(uhr.current);
      uhr.current = null;
    }
  }, [wartet]);
  useEffect(() => () => {
    if (uhr.current) clearTimeout(uhr.current);
  }, []);

  const senden = (anfrage: ModellzugangAnfrage, art: SchluesselArt, aktion: Wartet["aktion"]) => {
    if (uhr.current) clearTimeout(uhr.current);
    setWartet({ art, aktion });
    setHinweis(null);
    uhr.current = setTimeout(() => {
      setWartet(null);
      setHinweis({ art, text: "Keine Antwort vom Kern. Bitte später noch einmal versuchen.", fehler: true });
    }, ANTWORT_MS);
    sende(anfrage);
  };

  /** true: an den Kern gesendet; false: lokal abgelehnt (Format), nichts gesendet */
  const hinterlegen = (art: SchluesselArt, wert: string, ws: string): boolean => {
    const muster = art === "api" ? API_MUSTER : TOKEN_MUSTER;
    if (!muster.test(wert)) {
      // Ohne den Wert zu nennen: das richtige Feld erkennt SchluesselFormular schon am Anfang (falschesFeld)
      setHinweis({
        art,
        text:
          art === "api"
            ? "Das ist kein Anthropic-API-Schlüssel (er beginnt mit sk-ant-api und hat keine Leerzeichen)."
            : "Das ist kein Claude-Code-Token (er beginnt mit sk-ant-oat und hat keine Leerzeichen).",
        fehler: true,
      });
      return false;
    }
    if (art === "api" && ws && !WORKSPACE_MUSTER.test(ws)) {
      setHinweis({ art, text: "Die Workspace-ID beginnt mit wrkspc_.", fehler: true });
      return false;
    }
    senden(
      art === "api"
        ? ws
          ? { aktion: "hinterlegen", schluessel: wert, workspace: ws, schluesselart: "api" }
          : { aktion: "hinterlegen", schluessel: wert, schluesselart: "api" }
        : { aktion: "hinterlegen", schluessel: wert, schluesselart: "code" },
      art,
      "hinterlegen",
    );
    return true;
  };

  const festgelegt = zugang?.festgelegt === true;
  const hinterlegt = zugang?.hinterlegt === true;

  // Stand nicht lesbar (Kern nicht erreichbar, Anmeldung abgelaufen, Datenbank weg): konkreter Satz und neuer Versuch
  const standFehler = zugang && !zugang.art && zugang.fehler ? zugang.fehler : null;

  // Was für das Gespräch gerade gilt (nur der Stand, nie der Schlüssel)
  const wirkungGespraech =
    hinterlegt && zugang?.quelle === "wahl"
      ? "Im Modus Nur Hausmodelle wird der Schlüssel nicht benutzt."
      : hinterlegt
        ? zugang?.art === "claude" && zugang.modell
          ? `Das Gespräch läuft mit ${zugang.modell}.`
          : undefined
        : zugang?.quelle === "zuordnung"
          ? "Kein eigener Schlüssel hinterlegt; dein Profil nutzt den Schlüssel des Kerns."
          : "Ohne eigenen Schlüssel laufen Gespräch, Lernen, Zusammenfassung, Bilder und Websuche über die Hausmodelle.";

  return (
    <div className="mz-inhalt">
      {!online && (
        <p className="mz-offline" role="status">
          Gerade läuft kein Gespräch. Du kannst Schlüssel, Token und Modus trotzdem hier einstellen: Die Änderung wird
          direkt am Kern gespeichert und gilt ab der nächsten Sitzung.
        </p>
      )}
      {standFehler && (
        <div className="mz-falsch" role="alert">
          <p className="mz-meldung" data-fehler>
            Fehler: {standFehler}
          </p>
          <button
            type="button"
            className="btn-secondary btn-small"
            onClick={() => {
              // Stand löschen und neu fragen: die Hauptansicht holt ihn dann wieder (useModellzugangStandHalten)
              wamo.set((s) => ({ ...s, modellzugang: null }));
            }}
          >
            Erneut versuchen
          </button>
        </div>
      )}
      {festgelegt ? (
        <p className="mz-offline">
          Ein Admin hat dein Profil auf Hausmodelle festgelegt. Eigene Schlüssel und Token sind nicht möglich.
        </p>
      ) : (
        <>
          <SchluesselFormular
            art="api"
            ueberschrift="Gespräch"
            zweck="Claude-API-Schlüssel: Das Gespräch läuft mit Claude Haiku, bezahlt nach Verbrauch von deinem Guthaben."
            wirkung={wirkungGespraech}
            anleitung={API_ANLEITUNG}
            beschriftung="Anthropic-API-Schlüssel"
            platzhalter="sk-ant-api…"
            hinterlegt={hinterlegt}
            ende={zugang?.ende}
            gesetzt={zugang?.geprueft}
            wartet={wartet}
            hinweis={hinweis}
            workspace
            onHinterlegen={(wert, ws) => hinterlegen("api", wert, ws)}
            onUmleiten={(art, wert) => hinterlegen(art, wert, "")}
            onLoeschen={() => senden({ aktion: "loeschen", schluesselart: "api" }, "api", "loeschen")}
          />
          <SchluesselFormular
            art="code"
            ueberschrift="Entwicklungsaufträge"
            zweck="Claude-Code-Token: Entwicklungsaufträge laufen über das Kontingent deines Max-Tarifs, nicht über das Gespräch."
            anleitung={TOKEN_ANLEITUNG}
            beschriftung="Claude-Code-Token"
            platzhalter="sk-ant-oat…"
            hinterlegt={zugang?.codeHinterlegt === true}
            ende={zugang?.codeEnde}
            gesetzt={zugang?.codeGesetzt}
            wartet={wartet}
            hinweis={hinweis}
            onHinterlegen={(wert) => hinterlegen("code", wert, "")}
            onUmleiten={(art, wert) => hinterlegen(art, wert, "")}
            onLoeschen={() => senden({ aktion: "loeschen", schluesselart: "code" }, "code", "loeschen")}
          />
        </>
      )}
      <section className="mz-abschnitt" aria-labelledby="modellzugang-modus">
        <h4 id="modellzugang-modus" className="mz-ueberschrift">
          Modus
        </h4>
        <p className="mz-zweck">Welche Modelle WAMO benutzen darf, unabhängig davon, welche Schlüssel hinterlegt sind.</p>
        <p className="mz-statuszeile">
          <span className="mz-art" data-art={zugang?.art}>
            {kopfzeile(zugang)}
          </span>
        </p>
        <ModellWahl senden={sende} />
      </section>
      <p className="mz-klein">
        Beides liegt verschlüsselt im Kern, wird nie angezeigt und nie in Gesprächen, Aufgaben oder im Gedächtnis
        gespeichert. API-Schlüssel und Token sind getrennt: Der Token ersetzt den Schlüssel nicht. Änderungen gelten ab
        der nächsten Sitzung.
      </p>
    </div>
  );
});
