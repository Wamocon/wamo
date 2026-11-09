"use client";

import { createContext, useContext } from "react";

/** Was der Rahmen seinen Bausteinen anbietet: das Canvas und die Tafel "Aufträge" (Freigabekarten) ansteuern, am Handy über den Reiter. */
export type RahmenAngebot = { zeigeCanvas: () => void; zeigeAuftraege: () => void };

export const RahmenKontext = createContext<RahmenAngebot>({ zeigeCanvas: () => {}, zeigeAuftraege: () => {} });

export const useRahmen = () => useContext(RahmenKontext);
