"use client";

import { memo, useEffect, useState } from "react";
import { getSupabase } from "@/lib/supabase";
import { GERAET_STILL, geraeteAnzeige } from "@/lib/geraeteAnzeige";
import { useSelect } from "@/lib/store";
import { useBetriebsart } from "@/lib/betriebsart";
import {
  STATUS_INTERVALL_MS,
  einrichten,
  hinweisZeile,
  naechsteWartezeitMs,
  pruefeStatusAntwort,
} from "@/lib/geraet";
import { kernJson } from "@/lib/kernApi";

/**
 * Richtet den Laptop nach der Anmeldung selbst ein (Ablauf und Prüfungen in lib/geraet.ts, Dateien und Tunnel in der
 * Desktop-App, desktop/geraet.js). Nur in der Desktop-App und nur in der Betriebsart Büro; sonst passiert nichts.
 *
 * Der Ablauf startet einmal, sobald die Sitzung eine Benutzerkennung hat. Wartet das Gerät auf die Freigabe durch einen
 * Admin, wird er alle 30 s wiederholt. Fehler werden zunächst alle 30 s, nach 3 Fehlern in Folge nur noch alle
 * 5 Minuten wiederholt, nie ohne Pause. Ist das Gerät eingerichtet, fragt die Komponente jede Minute den Zustand von
 * Gerät und Tunnel ab; fehlt die Einrichtung dann (Datei gelöscht), läuft der Ablauf noch einmal.
 *
 * Unsichtbar, solange alles in Ordnung ist. Eine kurze Zeile erscheint nur bei "wartet", bei einem Fehler (mit dem
 * Text des Kerns) und solange der Tunnel nicht verbunden ist. Den vollen Stand zeigt die Einstellungsseite
 * (Abschnitt Geräte und PC); beide lesen denselben Store (lib/geraeteAnzeige.ts).
 */

export const GeraetEinrichtung = memo(function GeraetEinrichtung() {
  const betriebsart = useBetriebsart();
  const [userId, setUserId] = useState<string | null>(null);

  // Benutzerkennung der Sitzung (und ihr Ende)
  useEffect(() => {
    const supabase = getSupabase();
    if (!supabase) return;
    let aktiv = true;
    void supabase.auth.getSession().then(
      ({ data }) => {
        if (aktiv) setUserId(data.session?.user.id ?? null);
      },
      () => undefined,
    );
    const { data } = supabase.auth.onAuthStateChange((_ereignis, sitzung) => {
      if (aktiv) setUserId(sitzung?.user.id ?? null);
    });
    return () => {
      aktiv = false;
      data.subscription.unsubscribe();
    };
  }, []);

  useEffect(() => {
    const desktop = window.wamoDesktop?.geraet;
    if (!userId || betriebsart !== "buero" || !desktop) {
      geraeteAnzeige.set(() => GERAET_STILL);
      return;
    }
    let beendet = false;
    let uhr: ReturnType<typeof setTimeout> | undefined;
    let fehlerInFolge = 0;
    const spaeter = (ms: number, arbeit: () => Promise<void>) => {
      uhr = setTimeout(() => void arbeit(), ms);
    };

    // Zustand von Gerät und Tunnel, jede Minute (lokale Abfrage bei der Desktop-App)
    const pruefeZustand = async () => {
      let status = null;
      try {
        status = pruefeStatusAntwort(await desktop.status());
      } catch {
        // Desktop-App antwortet nicht: beim nächsten Mal wieder
      }
      if (beendet) return;
      if (status && !status.eingerichtet) {
        void durchlauf(); // Gerätedatei ist weg: neu einrichten
        return;
      }
      if (status) {
        // gleicher Stand: kein neues Objekt, damit die Abonnenten nicht jede Minute neu rendern
        geraeteAnzeige.set((alt) =>
          alt.zustand === "eingerichtet" && alt.tunnel === status.tunnel && alt.kennung === status.kennung
            ? alt
            : { zustand: "eingerichtet", tunnel: status.tunnel, kennung: status.kennung },
        );
      }
      spaeter(STATUS_INTERVALL_MS, pruefeZustand);
    };

    const durchlauf = async () => {
      if (beendet) return;
      const ergebnis = await einrichten({ desktop, kernJson, userId });
      if (beendet) return;
      fehlerInFolge = ergebnis.zustand === "fehler" ? fehlerInFolge + 1 : 0;
      geraeteAnzeige.set((alt) => ({
        zustand: ergebnis.zustand,
        text: ergebnis.text,
        tunnel: ergebnis.zustand === "eingerichtet" ? alt.tunnel : null,
        kennung: ergebnis.zustand === "eingerichtet" ? alt.kennung : null,
      }));
      const warten = naechsteWartezeitMs(ergebnis.zustand, fehlerInFolge);
      if (warten !== null) spaeter(warten, durchlauf);
      else if (ergebnis.zustand === "eingerichtet") void pruefeZustand();
    };

    void durchlauf();
    return () => {
      beendet = true;
      if (uhr !== undefined) clearTimeout(uhr);
    };
  }, [userId, betriebsart]);

  // Beim Abmelden bleibt kein Stand des früheren Profils zurück
  useEffect(() => () => geraeteAnzeige.set(() => GERAET_STILL), []);

  return <GeraetZeile />;
});

/** Die eine Zeile zum Stand der Geräteeinrichtung (nichts, solange alles in Ordnung ist); auch in den Meldungen der Einstellungen. */
export const GeraetZeile = memo(function GeraetZeile() {
  const anzeige = useSelect(geraeteAnzeige, (s) => s);
  const zeile = hinweisZeile(anzeige.zustand, anzeige.text, anzeige.tunnel);
  if (!zeile) return null;
  return (
    <p className="hinweis-toast" role="status" data-art="geraet">
      {zeile}
    </p>
  );
});
