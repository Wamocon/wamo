// DOM-Helfer der Oberflächen. Text aus dem Dienst kommt nie als HTML in die Seite: el() setzt Text nur über textContent und createTextNode,
// Attribute nur aus einer festen Liste (ERLAUBT), Ereignisse nur als Funktionen über addEventListener. Es gibt kein innerHTML, keine Ereignis-
// attribute, keinen Inline-Stil und keine Adressen als Attribut. So gilt die strenge Content-Security-Policy ohne Ausnahme.

const ERLAUBT = /^(type|id|role|tabindex|title|for|name|value|min|max|maxlength|minlength|autocomplete|inputmode|disabled|hidden|open|required|readonly|pattern|rows|cols|placeholder|spellcheck|aria-[a-z]+|data-[a-z0-9-]+)$/;

function anhaengen(knoten, kinder) {
  for (const kind of kinder) {
    if (kind === null || kind === undefined || kind === false) continue;
    knoten.append(typeof kind === "string" || typeof kind === "number" ? document.createTextNode(String(kind)) : kind);
  }
}

// el("button", { klasse: "knopf", text: "Ok", attr: { type: "button" }, ereignisse: { click: fn } }, ...kinder)
export function el(tag, optionen = {}, ...kinder) {
  const knoten = document.createElement(tag);
  const { klasse, text, attr, ereignisse } = optionen;
  if (klasse) knoten.setAttribute("class", klasse);
  for (const [name, wert] of Object.entries(attr ?? {})) {
    if (!ERLAUBT.test(name)) throw new TypeError(`Attribut nicht erlaubt: ${name}`);
    knoten.setAttribute(name, wert);
  }
  for (const [art, funktion] of Object.entries(ereignisse ?? {})) {
    if (typeof funktion !== "function") throw new TypeError(`Ereignis ${art} braucht eine Funktion`);
    knoten.addEventListener(art, funktion);
  }
  if (text !== undefined && text !== null) knoten.textContent = String(text);
  anhaengen(knoten, kinder);
  return knoten;
}

export function ersetzeKinder(knoten, ...kinder) {
  const neu = [];
  for (const kind of kinder) {
    if (kind === null || kind === undefined || kind === false) continue;
    neu.push(typeof kind === "string" || typeof kind === "number" ? document.createTextNode(String(kind)) : kind);
  }
  knoten.replaceChildren(...neu);
}

export const leeren = (knoten) => knoten.replaceChildren();
