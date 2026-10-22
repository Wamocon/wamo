"use client";

import { useId, type ReactNode } from "react";

/** Ein Block innerhalb eines Abschnitts: Karte mit kleiner Überschrift der Stufe 4 (die Abschnittsüberschrift ist Stufe 3). */
export function Block({ titel, children }: { titel: string; children: ReactNode }) {
  const id = useId();
  return (
    <section className="ez-block" aria-labelledby={id}>
      <h4 id={id} className="ez-block-titel">
        {titel}
      </h4>
      {children}
    </section>
  );
}

/** Beschriftungsliste (Name links, Wert rechts) im Stil der Seitenleiste. */
export function Daten({ zeilen }: { zeilen: readonly { name: string; wert: ReactNode }[] }) {
  return (
    <dl className="ez-daten">
      {zeilen.map((z) => (
        <div key={z.name}>
          <dt>{z.name}</dt>
          <dd>{z.wert}</dd>
        </div>
      ))}
    </dl>
  );
}
