"use client";

import { memo, useEffect, useRef, useState, type ReactNode } from "react";
import { ModellWahl } from "@/components/ModellWahl";
import type { Modellzugang } from "@/lib/nachrichten";
import { useSelect } from "@/lib/store";
import type { ModellzugangAnfrage, SchluesselArt } from "@/lib/useWamo";
import { istOnline, wamo } from "@/lib/wamo";
import styles from "./ModellZugang.module.css";

// API-Schlüssel (sk-ant-api…) und Claude-Code-Token (sk-ant-oat…) sind verschiedene Dinge: jedes Feld nimmt nur seins
const API_MUSTER = /^sk-ant-(?!oat)[A-Za-z0-9_-]{20,250}$/;
const TOKEN_MUSTER = /^sk-ant-oat\d{2}-[A-Za-z0-9_-]{20,400}$/;
const WORKSPACE_MUSTER = /^wrkspc_[A-Za-z0-9]{6,60}$/;
const ANTWORT_MS = 20_000; // Testanfrage bei Anthropic plus Datenbank

function kopfzeile(z: Modellzugang | null): string {
  if (!z?.art) return "Modellzugang";
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
  ueberschrift: string;
  erklaerung: ReactNode;
  beschriftung: string;
  platzhalter: string;
  hinterlegt: boolean;
  ende?: string;
  gesetzt?: string;
  wartet: Wartet | null;
  hinweis: Hinweis | null;
  workspace?: boolean;
  onHinterlegen: (wert: string, workspace: string) => void;
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
  const idKopf = `schluessel-${p.art}`;
  const hinweis = p.hinweis?.art === p.art ? p.hinweis : null;

  return (
    <section className={styles.abschnitt} aria-labelledby={idKopf}>
      <h3 id={idKopf} className={styles.ueberschrift}>
        {p.ueberschrift}
      </h3>
      <p className={styles.text}>
        {p.hinterlegt
          ? `Hinterlegt, endet auf …${p.ende ?? "????"}${zeitText(p.gesetzt)}.`
          : "Noch nichts hinterlegt."}
      </p>
      <p className={styles.klein}>{p.erklaerung}</p>
      <form
        className={styles.form}
        autoComplete="off"
        onSubmit={(e) => {
          e.preventDefault();
          p.onHinterlegen(wert.trim(), workspace.trim());
          setWert(""); // der Wert bleibt nicht in der Oberfläche
        }}
      >
        <label className={styles.feld}>
          <span>{p.hinterlegt ? `Neu: ${p.beschriftung}` : p.beschriftung}</span>
          <input
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
            disabled={p.wartet !== null}
          />
        </label>
        {p.workspace && (
          <details className={styles.mehr}>
            <summary>Organisationsschlüssel mit Workspace</summary>
            <label className={styles.feld}>
              <span>Workspace-ID (nur wenn nötig)</span>
              <input
                type="text"
                name="wamo-workspace"
                value={workspace}
                onChange={(e) => setWorkspace(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                placeholder="wrkspc_…"
                disabled={p.wartet !== null}
              />
            </label>
          </details>
        )}
        <div className={styles.knoepfe}>
          <button type="submit" className="btn-primary btn-small" disabled={p.wartet !== null || !wert.trim()}>
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
                  type="button"
                  className="btn-secondary btn-small"
                  disabled={p.wartet !== null}
                  onClick={() => {
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
                type="button"
                className="btn-secondary btn-small"
                disabled={p.wartet !== null}
                onClick={() => setLoeschenFragen(true)}
              >
                Löschen
              </button>
            ))}
        </div>
      </form>
      <p className={styles.meldung} role="status" data-fehler={hinweis?.fehler || undefined}>
        {hinweis?.text}
      </p>
    </section>
  );
}

/**
 * Modellzugang des Profils. Oben der Umschalter Automatisch oder Nur Hausmodelle (ModellWahl), darunter zwei
 * getrennte Schlüsseltypen: der Anthropic-API-Schlüssel für das Gespräch (bezahlt nach Verbrauch, Standardmodell
 * Haiku) und der Claude-Code-Token für Entwicklungsaufträge (aus dem Max-Tarif). Beide gehen einmal über die laufende
 * Sitzung an den eigenen Kern, der sie verschlüsselt speichert; der API-Schlüssel wird vorher mit einer kleinen Anfrage
 * geprüft, der Token nur auf sein Format (eine Anfrage würde das Kontingent verbrauchen). Nichts kommt zurück: es
 * erscheinen nur "hinterlegt" und die letzten vier Zeichen.
 */
export const ModellZugang = memo(function ModellZugang({
  onAnfrage,
}: {
  onAnfrage?: (anfrage: ModellzugangAnfrage) => boolean;
}) {
  const online = useSelect(wamo, istOnline);
  const zugang = useSelect(wamo, (s) => s.modellzugang);
  const [wartet, setWartet] = useState<Wartet | null>(null);
  const [hinweis, setHinweis] = useState<Hinweis | null>(null);
  const uhr = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Stand einmal je Verbindung abfragen (der Kern schickt ihn nicht von sich aus)
  useEffect(() => {
    if (online && !zugang && onAnfrage) onAnfrage({ aktion: "status" });
  }, [online, zugang, onAnfrage]);

  // Antwort des Kerns beendet das Warten (die Wahl des Modus bearbeitet ModellWahl). Abgeleiteter Zustand während des
  // Renderns statt in einem Effekt: React empfiehlt das für "auf eine neue Antwort reagieren".
  const [gesehen, setGesehen] = useState(zugang);
  if (zugang !== gesehen) {
    setGesehen(zugang);
    if (zugang && wartet && zugang.aktion !== "modus") {
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

  if (!onAnfrage) return null;

  const senden = (anfrage: ModellzugangAnfrage, art: SchluesselArt, aktion: Wartet["aktion"]) => {
    if (!onAnfrage(anfrage)) {
      setHinweis({ art, text: "Nicht gesendet: keine Verbindung zum Kern.", fehler: true });
      return;
    }
    setWartet({ art, aktion });
    setHinweis(null);
    uhr.current = setTimeout(() => {
      setWartet(null);
      setHinweis({ art, text: "Keine Antwort vom Kern. Bitte später noch einmal versuchen.", fehler: true });
    }, ANTWORT_MS);
  };

  const hinterlegen = (art: SchluesselArt, wert: string, ws: string) => {
    const muster = art === "api" ? API_MUSTER : TOKEN_MUSTER;
    if (!muster.test(wert)) {
      setHinweis({
        art,
        text:
          art === "api"
            ? TOKEN_MUSTER.test(wert)
              ? "Das ist ein Claude-Code-Token. Bitte im Feld darunter eintragen."
              : "Das ist kein Anthropic-API-Schlüssel (er beginnt mit sk-ant-)."
            : API_MUSTER.test(wert)
              ? "Das ist ein API-Schlüssel. Bitte im Feld darüber eintragen."
              : "Das ist kein Claude-Code-Token (er beginnt mit sk-ant-oat).",
        fehler: true,
      });
      return;
    }
    if (art === "api" && ws && !WORKSPACE_MUSTER.test(ws)) {
      setHinweis({ art, text: "Die Workspace-ID beginnt mit wrkspc_.", fehler: true });
      return;
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
  };

  const festgelegt = zugang?.festgelegt === true;
  const hinterlegt = zugang?.hinterlegt === true;

  return (
    <details className={styles.box}>
      <summary className={styles.kopf}>
        <span className="eyebrow">Modellzugang</span>
        <span className={styles.art} data-art={zugang?.art}>
          {kopfzeile(zugang)}
        </span>
      </summary>
      {!online ? (
        <p className={styles.text}>Verbinde dich mit WAMO, um den Modellzugang zu sehen oder zu ändern.</p>
      ) : (
        <div className={styles.inhalt}>
          <ModellWahl onAnfrage={onAnfrage} />
          <p className={styles.text}>
            {hinterlegt && zugang?.quelle === "wahl"
              ? `Eigener API-Schlüssel hinterlegt, endet auf …${zugang?.ende ?? "????"}. Im Modus Nur Hausmodelle wird er nicht benutzt.`
              : hinterlegt
                ? `Eigener API-Schlüssel hinterlegt, endet auf …${zugang?.ende ?? "????"}${zeitText(zugang?.geprueft, "geprüft am")}.`
                : zugang?.quelle === "zuordnung"
                  ? "Kein eigener Schlüssel hinterlegt; dein Profil nutzt den Schlüssel des Kerns."
                  : festgelegt
                    ? "Ein Admin hat dein Profil auf Hausmodelle festgelegt. Eigene Schlüssel sind nicht möglich."
                    : "Kein eigener API-Schlüssel: Gespräch, Lernen, Zusammenfassung, Bilder und Websuche laufen über die Hausmodelle."}
            {zugang?.art === "claude" && zugang.modell ? ` Modell: ${zugang.modell}.` : ""}
          </p>
          {!festgelegt && (
            <>
              <SchluesselFormular
                art="api"
                ueberschrift="Claude-API-Schlüssel (Gespräch, bezahlt nach Verbrauch)"
                erklaerung="Erzeugen unter console.anthropic.com im Bereich API-Schlüssel. Der Kern prüft ihn mit einer kleinen Anfrage. Mit ihm läuft das Gespräch auf Claude Haiku; Sonnet kostet ein Vielfaches."
                beschriftung="Anthropic-API-Schlüssel"
                platzhalter="sk-ant-api…"
                hinterlegt={hinterlegt}
                ende={zugang?.ende}
                gesetzt={zugang?.geprueft}
                wartet={wartet}
                hinweis={hinweis}
                workspace
                onHinterlegen={(wert, ws) => hinterlegen("api", wert, ws)}
                onLoeschen={() => senden({ aktion: "loeschen", schluesselart: "api" }, "api", "loeschen")}
              />
              <SchluesselFormular
                art="code"
                ueberschrift="Claude-Code-Token (Entwicklungsaufträge, aus dem Max-Tarif)"
                erklaerung={
                  <>
                    Erzeugen im Terminal mit <code>claude setup-token</code>, angemeldet mit deinem Claude-Konto. Der
                    Token gilt nur für Entwicklungsaufträge mit Claude Code, nicht für das Gespräch. Es gibt keine
                    Testanfrage, damit dein Kontingent nicht verbraucht wird; WAMO prüft nur das Format.
                  </>
                }
                beschriftung="Claude-Code-Token"
                platzhalter="sk-ant-oat…"
                hinterlegt={zugang?.codeHinterlegt === true}
                ende={zugang?.codeEnde}
                gesetzt={zugang?.codeGesetzt}
                wartet={wartet}
                hinweis={hinweis}
                onHinterlegen={(wert) => hinterlegen("code", wert, "")}
                onLoeschen={() => senden({ aktion: "loeschen", schluesselart: "code" }, "code", "loeschen")}
              />
            </>
          )}
          <p className={styles.klein}>
            Beides liegt verschlüsselt im Kern, wird nie angezeigt und nie in Gesprächen, Aufgaben oder im Gedächtnis
            gespeichert. API-Schlüssel und Token sind getrennt: Der Token ersetzt den Schlüssel nicht. Änderungen
            gelten ab der nächsten Sitzung.
          </p>
        </div>
      )}
    </details>
  );
});
