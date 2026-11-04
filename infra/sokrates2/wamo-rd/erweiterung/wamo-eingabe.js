/*
 * WAMO Eingabe: Tastaturleiste für die Fernsitzung (Erweiterung für Apache Guacamole 1.6, nur diese Herkunft).
 *
 * Zeigt in einer Sitzung unten rechts einen Tastatur-Knopf. Er öffnet eine Leiste mit einem Eingabefeld (am Handy öffnet
 * sich dort die Bildschirmtastatur des Geräts) und den Tasten, die dort fehlen: Esc, Tab, Pfeile, Entf, Pos1, Ende, Strg, Alt
 * und Win (zum Einrasten). Alles geht über die vorhandene Verbindung (Guacamole.Client.sendKeyEvent), es entsteht kein neuer
 * Weg nach draußen, und es wird nichts gespeichert oder protokolliert. Auch mit der Tastatur des Laptops nutzbar.
 */
(function (wurzel) {
    'use strict';

    var TASTEN = {
        Enter: 0xFF0D, Backspace: 0xFF08, Tab: 0xFF09, Escape: 0xFF1B, Delete: 0xFFFF,
        Home: 0xFF50, End: 0xFF57, PageUp: 0xFF55, PageDown: 0xFF56,
        Left: 0xFF51, Up: 0xFF52, Right: 0xFF53, Down: 0xFF54,
        Control: 0xFFE3, Alt: 0xFFE9, Super: 0xFFEB, Shift: 0xFFE1
    };
    // Tasten des Browsers, die als Taste (nicht als Zeichen) gesendet werden
    var BROWSER_TASTEN = {
        Enter: 'Enter', Backspace: 'Backspace', Tab: 'Tab', Escape: 'Escape', Delete: 'Delete', Home: 'Home', End: 'End',
        PageUp: 'PageUp', PageDown: 'PageDown', ArrowLeft: 'Left', ArrowUp: 'Up', ArrowRight: 'Right', ArrowDown: 'Down'
    };

    /** Guacamole-Tastencode (X11 keysym) eines Zeichens; null für Steuerzeichen ohne Taste. */
    function keysymFuer(zeichen) {
        var cp = String(zeichen).codePointAt(0);
        if (cp === 10 || cp === 13) { return TASTEN.Enter; }
        if (cp === 9) { return TASTEN.Tab; }
        if (cp === 8) { return TASTEN.Backspace; }
        if (cp < 0x20 || cp === 0x7F || (cp >= 0x80 && cp < 0xA0)) { return null; }
        if (cp <= 0xFF) { return cp; }
        return 0x01000000 + cp;
    }

    /** Was muss gelöscht und getippt werden, damit aus alt der Text neu wird (zeichenweise, nur der Schwanz ändert sich). */
    function unterschied(alt, neu) {
        var a = Array.from(alt), n = Array.from(neu), i = 0;
        while (i < a.length && i < n.length && a[i] === n[i]) { i++; }
        return { loeschen: a.length - i, text: n.slice(i).join('') };
    }

    /** Sendet eine Taste (drücken und loslassen), mit den eingerasteten Umschalttasten davor und danach. */
    function taste(client, keysym, umschalter) {
        var i;
        for (i = 0; i < umschalter.length; i++) { client.sendKeyEvent(1, umschalter[i]); }
        client.sendKeyEvent(1, keysym);
        client.sendKeyEvent(0, keysym);
        for (i = umschalter.length - 1; i >= 0; i--) { client.sendKeyEvent(0, umschalter[i]); }
    }

    /** Sendet Text Zeichen für Zeichen. Zeichen ohne Taste werden übersprungen. */
    function text(client, folge, umschalter) {
        Array.from(folge).forEach(function (z) {
            var k = keysymFuer(z);
            if (k !== null) { taste(client, k, umschalter); }
        });
    }

    var api = { keysymFuer: keysymFuer, unterschied: unterschied, taste: taste, text: text, TASTEN: TASTEN };
    if (typeof module !== 'undefined' && module.exports) { module.exports = api; }
    if (!wurzel || !wurzel.document || !wurzel.angular) { return; }

    // ---------- Oberfläche (nur im Browser) ----------
    var doc = wurzel.document;
    var eingerastet = { Control: false, Alt: false, Super: false };
    var knopf, leiste, feld, gesendet = '', schreibt = false;

    function aktuellerClient() {
        try {
            var inj = wurzel.angular.element(doc.body).injector();
            if (!inj) { return null; }
            var alle = inj.get('guacClientManager').getManagedClients() || {};
            var gefunden = null;
            Object.keys(alle).forEach(function (id) {
                var m = alle[id];
                if (m && m.client && typeof m.client.sendKeyEvent === 'function') {
                    var zustand = m.clientState && m.clientState.connectionState;
                    if (zustand === 'CONNECTED' || gefunden === null) { gefunden = m.client; }
                }
            });
            return gefunden;
        } catch (e) {
            return null;
        }
    }

    function umschalterListe() {
        var l = [];
        ['Control', 'Alt', 'Super'].forEach(function (n) { if (eingerastet[n]) { l.push(TASTEN[n]); } });
        return l;
    }

    function einrastenLoesen() {
        ['Control', 'Alt', 'Super'].forEach(function (n) { eingerastet[n] = false; });
        Array.prototype.forEach.call(leiste.querySelectorAll('[data-umschalter]'), function (b) {
            b.setAttribute('aria-pressed', 'false');
        });
    }

    function senden(fn) {
        var c = aktuellerClient();
        if (!c) { return; }
        fn(c, umschalterListe());
        einrastenLoesen();
    }

    function beiEingabe() {
        var d = unterschied(gesendet, feld.value);
        if (d.loeschen > 0 || d.text) {
            senden(function (c, u) {
                var i;
                for (i = 0; i < d.loeschen; i++) { taste(c, TASTEN.Backspace, []); }
                text(c, d.text, u);
            });
        }
        gesendet = feld.value;
        if (!schreibt) { feld.value = ''; gesendet = ''; }
    }

    function knopfBauen(beschriftung, name, art) {
        var b = doc.createElement('button');
        b.type = 'button';
        b.textContent = beschriftung;
        b.className = 'wamo-tk-taste';
        b.setAttribute('aria-label', name);
        if (art === 'umschalter') {
            b.setAttribute('data-umschalter', name);
            b.setAttribute('aria-pressed', 'false');
        }
        b.addEventListener('mousedown', function (e) { e.preventDefault(); }); // der Fokus bleibt im Eingabefeld
        return b;
    }

    function tasteMit(beschriftung, name, taste_) {
        var b = knopfBauen(beschriftung, name);
        b.addEventListener('click', function () { senden(function (c, u) { taste(c, TASTEN[taste_], u); }); });
        return b;
    }

    function aufbauen() {
        knopf = doc.createElement('button');
        knopf.type = 'button';
        knopf.className = 'wamo-tk-knopf';
        knopf.hidden = true;
        knopf.setAttribute('aria-label', 'Tastatur ein- oder ausblenden');
        knopf.setAttribute('aria-expanded', 'false');
        knopf.textContent = '⌨';
        knopf.addEventListener('click', function () {
            var offen = leiste.hidden;
            leiste.hidden = !offen;
            knopf.setAttribute('aria-expanded', String(offen));
            if (offen) { feld.focus(); }
        });

        leiste = doc.createElement('div');
        leiste.className = 'wamo-tk';
        leiste.hidden = true;
        leiste.setAttribute('role', 'group');
        leiste.setAttribute('aria-label', 'Tastatur');

        var zeile1 = doc.createElement('div');
        zeile1.className = 'wamo-tk-zeile';
        feld = doc.createElement('input');
        feld.type = 'text';
        feld.className = 'wamo-tk-feld';
        feld.setAttribute('aria-label', 'Text für den Spark');
        feld.setAttribute('placeholder', 'Hier tippen');
        ['autocomplete', 'autocorrect', 'autocapitalize'].forEach(function (a) { feld.setAttribute(a, 'off'); });
        feld.setAttribute('spellcheck', 'false');
        feld.setAttribute('enterkeyhint', 'send');
        feld.addEventListener('compositionstart', function () { schreibt = true; });
        feld.addEventListener('compositionend', function () { schreibt = false; beiEingabe(); });
        feld.addEventListener('input', beiEingabe);
        feld.addEventListener('keydown', function (e) {
            e.stopPropagation(); // Guacamole hört auf dem ganzen Dokument mit und würde jede Taste doppelt senden
            var t = BROWSER_TASTEN[e.key];
            if (!t || e.isComposing) { return; }
            e.preventDefault();
            senden(function (c, u) { taste(c, TASTEN[t], u); });
        });
        ['keyup', 'keypress'].forEach(function (n) { feld.addEventListener(n, function (e) { e.stopPropagation(); }); });
        zeile1.appendChild(feld);
        zeile1.appendChild(tasteMit('⌫', 'Rücktaste', 'Backspace'));
        zeile1.appendChild(tasteMit('⏎', 'Eingabe', 'Enter'));

        var zeile2 = doc.createElement('div');
        zeile2.className = 'wamo-tk-zeile';
        [['Strg', 'Control'], ['Alt', 'Alt'], ['Win', 'Super']].forEach(function (p) {
            var b = knopfBauen(p[0], p[1], 'umschalter');
            b.addEventListener('click', function () {
                eingerastet[p[1]] = !eingerastet[p[1]];
                b.setAttribute('aria-pressed', String(eingerastet[p[1]]));
            });
            zeile2.appendChild(b);
        });
        [['Esc', 'Escape', 'Escape'], ['Tab', 'Tab', 'Tab'], ['Entf', 'Entfernen', 'Delete']].forEach(function (p) {
            zeile2.appendChild(tasteMit(p[0], p[1], p[2]));
        });

        var zeile3 = doc.createElement('div');
        zeile3.className = 'wamo-tk-zeile';
        [['←', 'Pfeil links', 'Left'], ['↑', 'Pfeil hoch', 'Up'], ['↓', 'Pfeil runter', 'Down'],
         ['→', 'Pfeil rechts', 'Right'], ['Pos1', 'Position 1', 'Home'], ['Ende', 'Ende', 'End'],
         ['Bild↑', 'Bild hoch', 'PageUp'], ['Bild↓', 'Bild runter', 'PageDown']].forEach(function (p) {
            zeile3.appendChild(tasteMit(p[0], p[1], p[2]));
        });

        leiste.appendChild(zeile1);
        leiste.appendChild(zeile2);
        leiste.appendChild(zeile3);
        doc.body.appendChild(leiste);
        doc.body.appendChild(knopf);

        // Am Handy schiebt die Bildschirmtastatur die sichtbare Fläche zusammen: die Leiste bleibt darüber
        var vv = wurzel.visualViewport;
        if (vv) {
            var anpassen = function () {
                var unten = Math.max(0, wurzel.innerHeight - vv.height - vv.offsetTop);
                leiste.style.bottom = unten + 'px';
                knopf.style.bottom = (unten + 12) + 'px';
            };
            vv.addEventListener('resize', anpassen);
            vv.addEventListener('scroll', anpassen);
        }

        wurzel.setInterval(function () {
            var da = aktuellerClient() !== null;
            knopf.hidden = !da;
            if (!da && !leiste.hidden) { leiste.hidden = true; knopf.setAttribute('aria-expanded', 'false'); }
        }, 1000);
    }

    if (doc.readyState === 'loading') { doc.addEventListener('DOMContentLoaded', aufbauen); } else { aufbauen(); }
})(typeof window !== 'undefined' ? window : null);
