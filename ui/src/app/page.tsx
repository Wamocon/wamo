"use client";

import dynamic from "next/dynamic";
import { useSyncExternalStore } from "react";
import { LoginGate, type Sitzung } from "@/components/LoginGate";
import { WamoAnsicht } from "@/components/WamoAnsicht";
import { useWamo } from "@/lib/useWamo";

/**
 * Entwicklungsvorschau (?vorschau=1 bzw. ?vorschau=anmeldung): nur bei `next dev` UND mit
 * NEXT_PUBLIC_WAMO_VORSCHAU=1. Beide Werte setzt Next.js zur Build-Zeit ein; im Produktions-Build
 * ist die Bedingung falsch und das Modul wird gar nicht gebündelt.
 */
const Vorschau =
  process.env.NODE_ENV === "development" && process.env.NEXT_PUBLIC_WAMO_VORSCHAU === "1"
    ? dynamic(() => import("@/components/Vorschau").then((m) => m.Vorschau), { ssr: false })
    : null;

const keinAbo = () => () => {};

function useVorschauModus(): string | null {
  return useSyncExternalStore(
    keinAbo,
    () =>
      process.env.NODE_ENV === "development" && process.env.NEXT_PUBLIC_WAMO_VORSCHAU === "1"
        ? new URLSearchParams(window.location.search).get("vorschau")
        : null,
    () => null,
  );
}

export default function Home() {
  const vorschau = useVorschauModus();
  if (Vorschau && vorschau) return <Vorschau modus={vorschau} />;
  return <LoginGate>{(sitzung) => <Wamo sitzung={sitzung} />}</LoginGate>;
}

function Wamo({ sitzung }: { sitzung: Sitzung }) {
  const aktionen = useWamo();
  return <WamoAnsicht aktionen={aktionen} sitzung={sitzung} />;
}
