"use client";

import { useSyncExternalStore } from "react";

/**
 * Browser-Betrieb auf dem Server (Sokrates-2).
 *
 * Dieselbe Oberfläche läuft an zwei Orten: auf dem PC (Desktop-App oder Browser, Kern lokal oder im Büro) und im
 * Büro-LAN als Dienst auf Sokrates-2, den jedes Gerät im Browser öffnet. Für den zweiten Fall wird sie mit
 * NEXT_PUBLIC_WAMO_NUR_BUERO=ja gebaut (Build-Argument, infra/sokrates2/wamo-ui/wamo-ui.conf). Ein solcher Build
 * kennt keinen lokalen Kern: Er steht nicht in der CSP, und ein Browser darauf hat nur die Betriebsart Büro. PC-Werkzeuge
 * gehören zur Desktop-App, deshalb zeigt der Browser dort statt des Schalters einen Hinweis.
 *
 * Erkannt wird die Desktop-App an window.wamoDesktop (preload.js), nie an der Adresse: Dieselbe Adresse kann in
 * Zukunft von einer Domain bedient werden (Variante B), ohne dass sich hier etwas ändert.
 */

/** Wahr im Build für Sokrates-2 (Build-Konstante, auf Server und Client gleich). */
export const NUR_BUERO_BUILD = nurBueroBuild(process.env.NEXT_PUBLIC_WAMO_NUR_BUERO);

/** Auswertung des Build-Arguments: nur "ja" (ohne Beachtung von Groß- und Kleinschreibung) schaltet ein. */
export function nurBueroBuild(wert: string | undefined): boolean {
  return (wert ?? "").trim().toLowerCase() === "ja";
}

/**
 * Browser auf dem Server: Build für Sokrates-2 und keine Desktop-App. Hier gibt es nur die Betriebsart Büro,
 * und die PC-Steuerung ist nicht verfügbar (sie läuft über die Desktop-App des PCs).
 */
export function istServerBrowser(nurBuero: boolean, desktop: boolean): boolean {
  return nurBuero && !desktop;
}

/** Aktueller Zustand dieses Fensters (im Server-Rendering immer Browser, also false ohne Build-Argument). */
export function serverBrowser(): boolean {
  const desktop = typeof window !== "undefined" && Boolean(window.wamoDesktop);
  return istServerBrowser(NUR_BUERO_BUILD, desktop);
}

const keinAbo = () => () => {};

/**
 * Für die Oberfläche. Der Server kann die Desktop-App nicht erkennen und nimmt im Build für Sokrates-2 den Browser
 * an; eine Desktop-App stellt das beim ersten Rendern auf dem Client richtig.
 */
export function useServerBrowser(): boolean {
  return useSyncExternalStore(keinAbo, serverBrowser, () => NUR_BUERO_BUILD);
}
