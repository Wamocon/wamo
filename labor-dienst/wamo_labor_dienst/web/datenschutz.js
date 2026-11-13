// Seite /datenschutz: nur der Knopf Drucken. Der Text steht fest in der Seite, die Seite fragt keinen Dienst und kennt weder Token noch Platzdaten.

document.getElementById("drucken").addEventListener("click", () => window.print());
