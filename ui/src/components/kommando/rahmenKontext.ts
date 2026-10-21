"use client";

import { createContext, useContext } from "react";

/** Was der Rahmen seinen Bausteinen anbietet (derzeit: das Canvas ansteuern, am Handy über den Reiter). */
export type RahmenAngebot = { zeigeCanvas: () => void };

export const RahmenKontext = createContext<RahmenAngebot>({ zeigeCanvas: () => {} });

export const useRahmen = () => useContext(RahmenKontext);
