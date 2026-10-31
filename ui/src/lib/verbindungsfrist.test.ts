// Tests für den Verbindungs-Watchdog (lib/verbindungsfrist.ts): 20 s ohne Betriebsmeldung, Verlängerung bei offener
// Mikrofonfrage, sauberes Aufräumen beim Trennen und Abmelden, kein Rennen mit einer neuen Verbindung.
// Ausführen im Ordner ui/:  npm test
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import {
  erstelleFrist,
  fristMeldung,
  MAX_VERLAENGERUNGEN,
  sollVerlaengern,
  VERBINDUNG_FRIST_MS,
} from "./verbindungsfrist.ts";

beforeEach(() => mock.timers.enable({ apis: ["setTimeout"] }));
afterEach(() => mock.timers.reset());

const fluessig = () => new Promise((r) => setImmediate(r));

function aufbau(opt: { verlaengern?: (n: number) => boolean | Promise<boolean> } = {}) {
  const zustand = { gen: 1, betrieb: false, abgelaufen: 0, gefragt: [] as number[] };
  const frist = erstelleFrist({
    gen: () => zustand.gen,
    fertig: () => zustand.betrieb,
    verlaengern: opt.verlaengern
      ? (n) => {
          zustand.gefragt.push(n);
          return opt.verlaengern!(n);
        }
      : undefined,
    beiAblauf: () => {
      zustand.abgelaufen += 1;
    },
  });
  return { zustand, frist };
}

test("ohne Betriebsmeldung läuft die Frist nach 20 s ab", async () => {
  const { zustand, frist } = aufbau();
  frist.starte(1);
  assert.equal(frist.laeuft(), true);
  mock.timers.tick(VERBINDUNG_FRIST_MS - 1);
  await fluessig();
  assert.equal(zustand.abgelaufen, 0);
  mock.timers.tick(1);
  await fluessig();
  assert.equal(zustand.abgelaufen, 1);
  assert.equal(frist.laeuft(), false);
});

test("kommt die Betriebsmeldung vor Ablauf, passiert nichts", async () => {
  const { zustand, frist } = aufbau();
  frist.starte(1);
  zustand.betrieb = true;
  mock.timers.tick(VERBINDUNG_FRIST_MS);
  await fluessig();
  assert.equal(zustand.abgelaufen, 0);
});

test("Trennen oder Abmelden (stoppe) beendet die Frist, nichts läuft nach", async () => {
  const { zustand, frist } = aufbau();
  frist.starte(1);
  frist.stoppe();
  assert.equal(frist.laeuft(), false);
  mock.timers.tick(VERBINDUNG_FRIST_MS * 3);
  await fluessig();
  assert.equal(zustand.abgelaufen, 0);
});

test("eine neue Generation macht die alte Frist wirkungslos (kein Rennen beim erneuten Verbinden)", async () => {
  const { zustand, frist } = aufbau();
  frist.starte(1);
  mock.timers.tick(VERBINDUNG_FRIST_MS - 1000);
  zustand.gen = 2; // Trennen und neu verbinden: teardown zählt hoch
  frist.starte(2);
  mock.timers.tick(1000); // hier wäre die erste Frist abgelaufen
  await fluessig();
  assert.equal(zustand.abgelaufen, 0);
  mock.timers.tick(VERBINDUNG_FRIST_MS - 1000);
  await fluessig();
  assert.equal(zustand.abgelaufen, 1, "die zweite Frist gilt ab ihrem Start");
});

test("ändert sich die Generation, ohne dass jemand stoppt, läuft die Frist ins Leere", async () => {
  const { zustand, frist } = aufbau();
  frist.starte(1);
  zustand.gen = 2;
  mock.timers.tick(VERBINDUNG_FRIST_MS);
  await fluessig();
  assert.equal(zustand.abgelaufen, 0);
});

test("starte ersetzt eine laufende Frist, es läuft nur eine", async () => {
  const { zustand, frist } = aufbau();
  frist.starte(1);
  mock.timers.tick(5000);
  frist.starte(1);
  mock.timers.tick(VERBINDUNG_FRIST_MS - 1);
  await fluessig();
  assert.equal(zustand.abgelaufen, 0);
  mock.timers.tick(1);
  await fluessig();
  assert.equal(zustand.abgelaufen, 1);
});

test("wird die Frist verlängert, läuft sie erst später ab", async () => {
  const { zustand, frist } = aufbau({ verlaengern: (n) => n < 2 });
  frist.starte(1);
  for (let i = 0; i < 3; i++) {
    mock.timers.tick(VERBINDUNG_FRIST_MS);
    await fluessig();
  }
  assert.deepEqual(zustand.gefragt, [0, 1, 2]);
  assert.equal(zustand.abgelaufen, 1);
});

test("während der Verlängerungsfrage getrennt: kein Ablauf mehr", async () => {
  let frei: (b: boolean) => void = () => undefined;
  const { zustand, frist } = aufbau({ verlaengern: () => new Promise<boolean>((r) => (frei = r)) });
  frist.starte(1);
  mock.timers.tick(VERBINDUNG_FRIST_MS);
  frist.stoppe(); // Abmelden, während der Browser noch antwortet
  frei(false);
  await fluessig();
  assert.equal(zustand.abgelaufen, 0);
});

test("Betriebsmeldung während der Verlängerungsfrage: kein Ablauf", async () => {
  let frei: (b: boolean) => void = () => undefined;
  const { zustand, frist } = aufbau({ verlaengern: () => new Promise<boolean>((r) => (frei = r)) });
  frist.starte(1);
  mock.timers.tick(VERBINDUNG_FRIST_MS);
  zustand.betrieb = true;
  frei(false);
  await fluessig();
  assert.equal(zustand.abgelaufen, 0);
});

test("scheitert die Verlängerungsfrage, gilt die Frist", async () => {
  const { zustand, frist } = aufbau({ verlaengern: () => Promise.reject(new Error("nicht lesbar")) });
  frist.starte(1);
  mock.timers.tick(VERBINDUNG_FRIST_MS);
  await fluessig();
  assert.equal(zustand.abgelaufen, 1);
});

test("verlängert wird nur, solange der Browser die Mikrofonfrage offen hat, und nicht endlos", () => {
  assert.equal(sollVerlaengern("prompt", 0), true);
  assert.equal(sollVerlaengern("prompt", MAX_VERLAENGERUNGEN - 1), true);
  assert.equal(sollVerlaengern("prompt", MAX_VERLAENGERUNGEN), false);
  assert.equal(sollVerlaengern("granted", 0), false);
  assert.equal(sollVerlaengern("denied", 0), false);
  assert.equal(sollVerlaengern(undefined, 0), false);
  assert.equal(sollVerlaengern(null, 0), false);
});

test("die Meldung nennt je Betriebsart, was zu prüfen ist, und hat weder Gedankenstrich noch Platzhalter", () => {
  const buero = fristMeldung("buero");
  const unterwegs = fristMeldung("unterwegs");
  assert.match(buero.text, /Büro-LAN|WireGuard/);
  assert.match(unterwegs.text, /Desktop-App/);
  for (const m of [buero, unterwegs, fristMeldung(null)]) {
    assert.equal(m.art, "verbindung", "art verbindung: die Oberfläche zeigt Erneut verbinden");
    assert.match(m.text, /20 Sekunden/);
    assert.doesNotMatch(m.text, /[–—]| - /);
  }
});
