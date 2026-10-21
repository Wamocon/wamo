"use client";

import { useEffect, useState } from "react";

/** Zwei Uhren desselben Moments: datum (Date.now) und perf (performance.now, wie startedAt der Werkzeuge). */
export type Takt = { datum: number; perf: number };

const jetzt = (): Takt => ({ datum: Date.now(), perf: performance.now() });

/** Aktuelle Zeit, alle `intervallMs` neu; hält nur die aufrufende kleine Komponente im Takt. */
export function useJetzt(intervallMs: number): Takt {
  const [takt, setTakt] = useState(jetzt);
  useEffect(() => {
    const id = setInterval(() => setTakt(jetzt()), intervallMs);
    return () => clearInterval(id);
  }, [intervallMs]);
  return takt;
}
