/**
 * Angaben zum laufenden Build für die Gesundheitsprobe der Oberfläche (GET /version.json, app/version.json/route.ts).
 *
 * Das Umschalten der Farben auf Sokrates-2 (infra/sokrates2/wamo-ui-umschalten.sh) fragt diese Datei beim neuen Container
 * ab, bevor es Caddy umstellt, und danach durch Caddy hindurch während der Bewährung. Sie belegt, dass wirklich der
 * freigegebene Stand läuft: Version aus package.json, Commit-ID und Bauzeit aus den Build-Argumenten des Dockerfiles.
 *
 * Es gibt nie ein Geheimnis darin. Jeder Wert wird gegen ein enges Muster geprüft, alles andere wird zu "unbekannt"
 * und nicht durchgereicht; so kann auch eine falsch gesetzte Umgebungsvariable nichts verraten. Das Format der Antwort
 * (Reihenfolge der Felder, kompakte Schreibweise) liest das Umschalt-Skript mit einem strengen Muster, es gehört zum
 * Vertrag mit infra/sokrates2/wamo-ui-umschalten.sh.
 */

export const UNBEKANNT = "unbekannt";

const VERSION_RE = /^(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})$/;
const COMMIT_RE = /^[0-9a-f]{40}$/;
const ZEIT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;

export type BuildRoh = {
  version?: string;
  commit?: string;
  gebaut?: string;
  sauber?: string;
};

export type BuildInfo = {
  name: "wamo-ui";
  version: string;
  commit: string;
  gebaut: string;
  sauber: boolean;
};

function nurWennGueltig(wert: string | undefined, muster: RegExp): string {
  return wert !== undefined && muster.test(wert) ? wert : UNBEKANNT;
}

/** Baut die Antwort aus den rohen Umgebungswerten. Reihenfolge der Felder ist Teil des Vertrags (siehe oben). */
export function buildInfo(roh: BuildRoh): BuildInfo {
  return {
    name: "wamo-ui",
    version: nurWennGueltig(roh.version, VERSION_RE),
    commit: nurWennGueltig(roh.commit, COMMIT_RE),
    gebaut: nurWennGueltig(roh.gebaut, ZEIT_RE),
    // "ja" nur bei einem Build aus unverändertem Git-Stand (verteilen prüft das vor dem Kopieren)
    sauber: roh.sauber === "ja",
  };
}
