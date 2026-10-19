import { useSyncExternalStore } from "react";

/**
 * Kleiner Store gegen unnötiges Neurendern: Komponenten abonnieren nur ihren Ausschnitt
 * (useSelect). Ein neues Satzstück im Untertitel rendert so nur den Untertitel, nicht Canvas
 * und Mermaid.
 */
export type Store<T> = {
  get: () => T;
  set: (update: (state: T) => T) => void;
  subscribe: (listener: () => void) => () => void;
};

export function createStore<T>(initial: T): Store<T> {
  let state = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => state,
    set: (update) => {
      const next = update(state);
      if (next !== state) {
        state = next;
        listeners.forEach((l) => l());
      }
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

/** Der Selektor muss eine stabile Referenz oder einen Primitivwert liefern (kein neues Objekt je Aufruf). */
export function useSelect<T, S>(store: Store<T>, select: (state: T) => S): S {
  return useSyncExternalStore(
    store.subscribe,
    () => select(store.get()),
    () => select(store.get()),
  );
}
