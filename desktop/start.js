// Lade- und Fehlerseite des Hauptfensters (desktop/start.html). main.js zeigt sie sofort beim Start,
// solange Voice-Core und Oberfläche hochfahren, und bei did-fail-load mit "Wiederholen".
// Abfrage: ?zustand=laden|bauen|fehler&ziel=<UI-Adresse>&code=<Fehlertext>
// "Wiederholen" navigiert zur UI-Adresse; main.js lässt nur deren Herkunft zu (will-navigate).

const q = new URLSearchParams(location.search);
const zustand = q.get("zustand") || "laden";
const ziel = q.get("ziel") || "";

if (zustand === "bauen") {
  document.getElementById("laden-text").textContent =
    "Die Oberfläche wird einmalig gebaut. Das dauert beim ersten Start etwa eine Minute …";
}

if (zustand === "fehler") {
  document.getElementById("laden").hidden = true;
  document.getElementById("fehler").hidden = false;
  const code = q.get("code");
  document.getElementById("fehler-text").textContent =
    `${ziel || "Die Adresse"} antwortet nicht${code ? ` (${code})` : ""}. Läuft die Oberfläche?`;
  const knopf = document.getElementById("wiederholen");
  knopf.addEventListener("click", () => {
    if (/^https?:\/\//.test(ziel)) location.href = ziel;
  });
  knopf.focus();
}
