"use client";

import { memo, useEffect, useRef, useState } from "react";
import type { Modellzugang } from "@/lib/nachrichten";
import { useSelect } from "@/lib/store";
import type { ModellzugangAnfrage } from "@/lib/useWamo";
import { istOnline, wamo } from "@/lib/wamo";
import styles from "./ModellZugang.module.css";

const SCHLUESSEL_MUSTER = /^sk-ant-[A-Za-z0-9_-]{20,250}$/;
const WORKSPACE_MUSTER = /^wrkspc_[A-Za-z0-9]{6,60}$/;
const ANTWORT_MS = 20_000; // Testanfrage bei Anthropic plus Datenbank

function kopfzeile(z: Modellzugang | null): string {
  if (!z?.art) return "Modellzugang";
  if (z.art === "haus") return z.festgelegt ? "Nur Hausmodelle (vom Admin festgelegt)" : "Nur Hausmodelle";
  return z.quelle === "zuordnung" ? "Claude über den Schlüssel des Kerns" : "Claude mit eigenem Schlüssel";
}

function geprueftText(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : `, geprüft am ${d.toLocaleDateString("de-DE")} um ${d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} Uhr`;
}

/**
 * Modellzugang des Profils: eigener Claude-Schlüssel (Standard Haiku) oder nur Hausmodelle. Der Schlüssel geht
 * einmal über die laufende Sitzung an den eigenen Kern, der ihn mit einer kleinen Anfrage prüft und verschlüsselt
 * speichert. Er bleibt nur bis zum Absenden in diesem Feld, wird nie im Speicher der Oberfläche abgelegt und kommt
 * nie zurück: angezeigt werden nur "hinterlegt" und die letzten vier Zeichen.
 */
export const ModellZugang = memo(function ModellZugang({
  onAnfrage,
}: {
  onAnfrage?: (anfrage: ModellzugangAnfrage) => boolean;
}) {
  const online = useSelect(wamo, istOnline);
  const zugang = useSelect(wamo, (s) => s.modellzugang);
  const [schluessel, setSchluessel] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [wartet, setWartet] = useState<ModellzugangAnfrage["aktion"] | null>(null);
  const [loeschenFragen, setLoeschenFragen] = useState(false);
  const [hinweis, setHinweis] = useState<{ text: string; fehler: boolean }>({ text: "", fehler: false });
  const uhr = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Stand einmal je Verbindung abfragen (der Kern schickt ihn nicht von sich aus)
  useEffect(() => {
    if (online && !zugang && onAnfrage) onAnfrage({ aktion: "status" });
  }, [online, zugang, onAnfrage]);

  // Antwort des Kerns beendet das Warten
  useEffect(() => {
    if (!zugang || !wartet) return;
    if (uhr.current) clearTimeout(uhr.current);
    setWartet(null);
    if (zugang.fehler) setHinweis({ text: zugang.fehler, fehler: true });
    else
      setHinweis({
        text:
          zugang.aktion === "loeschen"
            ? "Schlüssel gelöscht."
            : zugang.aktion === "hinterlegen"
              ? "Schlüssel geprüft und hinterlegt."
              : "",
        fehler: false,
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- nur auf neue Antworten reagieren
  }, [zugang]);

  useEffect(() => () => {
    if (uhr.current) clearTimeout(uhr.current);
  }, []);

  if (!onAnfrage) return null;

  const senden = (anfrage: ModellzugangAnfrage) => {
    if (!onAnfrage(anfrage)) {
      setHinweis({ text: "Nicht gesendet: keine Verbindung zum Kern.", fehler: true });
      return;
    }
    setWartet(anfrage.aktion);
    setHinweis({ text: "", fehler: false });
    uhr.current = setTimeout(() => {
      setWartet(null);
      setHinweis({ text: "Keine Antwort vom Kern. Bitte später noch einmal versuchen.", fehler: true });
    }, ANTWORT_MS);
  };

  const hinterlegen = () => {
    const wert = schluessel.trim();
    const ws = workspace.trim();
    if (!SCHLUESSEL_MUSTER.test(wert)) {
      setHinweis({ text: "Das ist kein Anthropic-API-Schlüssel (er beginnt mit sk-ant-).", fehler: true });
      return;
    }
    if (ws && !WORKSPACE_MUSTER.test(ws)) {
      setHinweis({ text: "Die Workspace-ID beginnt mit wrkspc_.", fehler: true });
      return;
    }
    senden(ws ? { aktion: "hinterlegen", schluessel: wert, workspace: ws } : { aktion: "hinterlegen", schluessel: wert });
    setSchluessel(""); // Feld sofort leeren, der Schlüssel bleibt nicht in der Oberfläche
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
          <p className={styles.text}>
            {hinterlegt
              ? `Eigener Schlüssel hinterlegt, endet auf …${zugang?.ende ?? "????"}${geprueftText(zugang?.geprueft)}.`
              : zugang?.quelle === "zuordnung"
                ? "Kein eigener Schlüssel hinterlegt; dein Profil nutzt den Schlüssel des Kerns."
                : festgelegt
                  ? "Ein Admin hat dein Profil auf Hausmodelle festgelegt. Ein eigener Schlüssel ist nicht möglich."
                  : "Kein eigener Schlüssel: Gespräch, Lernen, Zusammenfassung, Bilder und Websuche laufen über die Hausmodelle."}
            {zugang?.art === "claude" && zugang.modell ? ` Modell: ${zugang.modell}.` : ""}
          </p>
          {!festgelegt && (
            <form
              className={styles.form}
              autoComplete="off"
              onSubmit={(e) => {
                e.preventDefault();
                hinterlegen();
              }}
            >
              <label className={styles.feld}>
                <span>{hinterlegt ? "Neuer Anthropic-Schlüssel" : "Eigener Anthropic-Schlüssel"}</span>
                <input
                  type="password"
                  name="wamo-anthropic"
                  value={schluessel}
                  onChange={(e) => setSchluessel(e.target.value)}
                  autoComplete="new-password"
                  spellCheck={false}
                  autoCapitalize="off"
                  data-1p-ignore
                  data-lpignore="true"
                  placeholder="sk-ant-…"
                  disabled={wartet !== null}
                />
              </label>
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
                    disabled={wartet !== null}
                  />
                </label>
              </details>
              <div className={styles.knoepfe}>
                <button type="submit" className="btn-primary btn-small" disabled={wartet !== null || !schluessel.trim()}>
                  {wartet === "hinterlegen" ? "wird geprüft …" : hinterlegt ? "Ersetzen" : "Hinterlegen"}
                </button>
                {hinterlegt &&
                  (loeschenFragen ? (
                    <>
                      <button
                        type="button"
                        className="btn-secondary btn-small"
                        disabled={wartet !== null}
                        onClick={() => {
                          setLoeschenFragen(false);
                          senden({ aktion: "loeschen" });
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
                      disabled={wartet !== null}
                      onClick={() => setLoeschenFragen(true)}
                    >
                      Löschen
                    </button>
                  ))}
              </div>
            </form>
          )}
          <p className={styles.klein}>
            Beim Hinterlegen prüft der Kern den Schlüssel mit einer kleinen Anfrage und speichert ihn verschlüsselt. Er
            wird nie angezeigt. Änderungen gelten ab der nächsten Sitzung.
          </p>
          <p className={styles.meldung} role="status" data-fehler={hinweis.fehler || undefined}>
            {hinweis.text}
          </p>
        </div>
      )}
    </details>
  );
});
