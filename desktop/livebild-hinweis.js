// Hinweis am Laptop: zählt nur die Restzeit herunter (bis kommt als Query-Parameter, Millisekunden seit 1970).
(function () {
  "use strict";
  const bis = Number(new URLSearchParams(location.search).get("bis")) || 0;
  const rest = document.getElementById("rest");
  function zeigen() {
    const s = Math.max(0, Math.floor((bis - Date.now()) / 1000));
    rest.textContent = bis ? `Endet in ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : "";
  }
  zeigen();
  setInterval(zeigen, 1000);
})();
