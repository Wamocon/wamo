import { connection } from "next/server";
import { buildInfo } from "@/lib/buildInfo";

/**
 * Gesundheitsprobe der Oberfläche: GET /version.json (Format und Zweck in lib/buildInfo.ts).
 *
 * Die Route läuft bei jeder Anfrage (connection), damit die Probe wirklich den laufenden Server prüft und nie eine
 * Antwort aus dem Build-Cache liefert. Die Version setzt next.config.ts beim Build aus package.json ein; Commit-ID und
 * Bauzeit stehen als Umgebung im Image (Build-Argumente in infra/sokrates2/wamo-ui/Dockerfile). Kein Geheimnis,
 * keine Anfragedaten, nur GET.
 */
export async function GET(): Promise<Response> {
  await connection();
  return Response.json(
    buildInfo({
      version: process.env.WAMO_VERSION,
      commit: process.env.WAMO_COMMIT,
      gebaut: process.env.WAMO_BAUZEIT,
      sauber: process.env.WAMO_SAUBER,
    }),
    { headers: { "Cache-Control": "no-store" } },
  );
}
