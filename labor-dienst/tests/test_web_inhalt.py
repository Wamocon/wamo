"""Inhalt der Weboberflächen (statische Prüfung): Nutzertext nie als HTML, kein Inline-Skript und kein Inline-Stil (strenge CSP), keine fremden Adressen,
keine Token und keine Platzdaten in den Dateien, Corporate Design ohne Blau, Lesbarkeit (Kontrast, Fokus, Sprache)."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from hilfen import BETREUER_TOKEN, KERN_TOKEN

from wamo_labor_dienst import auslieferung
from wamo_labor_dienst.seed import SEED

WEB = Path(auslieferung.__file__).resolve().parent / "web"
DATEIEN = sorted(p for p in WEB.iterdir() if p.is_file())
HTML = [p for p in DATEIEN if p.suffix == ".html"]
JS = [p for p in DATEIEN if p.suffix == ".js"]
CSS = [p for p in DATEIEN if p.suffix == ".css"]
PALETTE = {"#F40E0E", "#FFFFFF", "#000000", "#F3F3F3", "#D9D9D9", "#6B6B6B"}  # Rot, Weiß, Schwarz, Grautöne (docs/labor-dienst-vertrag.md): kein Blau


def _text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_es_gibt_seiten_skripte_und_stil():
    assert HTML and JS and CSS


class _Pruefer(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fehler: list[str] = []
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a))
        if tag in ("style", "iframe", "object", "embed", "base", "frame", "applet"):
            self.fehler.append(f"verbotenes Element {tag}")
        if tag == "script" and (a.get("type") != "module" or not str(a.get("src", "")).startswith("/web/")):
            self.fehler.append("Skript ohne type=module und src von /web/ (Inline-Skript)")
        if tag == "meta" and str(a.get("http-equiv", "")).lower() == "refresh":
            self.fehler.append("meta refresh")
        for name, wert in attrs:
            if name.startswith("on") or name in ("style", "srcdoc", "formaction", "action"):
                self.fehler.append(f"Attribut {name}")
            if name in ("href", "src") and not re.fullmatch(r"/web/[a-z0-9-]+\.(css|js)|#[A-Za-z0-9_-]*|data:,", str(wert)):
                self.fehler.append(f"Adresse {wert}")
            if wert and re.search(r"javascript:|vbscript:|data:text|<script", str(wert), re.I):
                self.fehler.append(f"Wert {wert}")

    def handle_data(self, data):
        pass


@pytest.mark.parametrize("seite", HTML, ids=lambda p: p.name)
def test_seite_hat_kein_inline_skript_keinen_inline_stil_keine_ereignisattribute(seite):
    p = _Pruefer()
    p.feed(_text(seite))
    assert p.fehler == []
    skripte = [a for t, a in p.tags if t == "script"]
    assert skripte, "die Seite braucht ihr Modul"
    html = _text(seite)
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", html) and "</script>" not in re.sub(r"<script[^>]*src=[^>]*>\s*</script>", "", html)
    assert not re.search(r"\bstyle\s*=", html) and not re.search(r"\son[a-z]+\s*=", html, re.I)


@pytest.mark.parametrize("seite", HTML, ids=lambda p: p.name)
def test_seite_ist_deutsch_lesbar_und_vollstaendig(seite):
    html = _text(seite)
    assert html.lstrip().lower().startswith("<!doctype html>") and '<html lang="de">' in html
    assert '<meta charset="utf-8">' in html and 'name="viewport"' in html and re.search(r"<title>[^<]{5,}</title>", html)
    assert '<link rel="stylesheet" href="/web/stil.css">' in html
    assert "WAMOCON Test-Jira" in html and "Atlassian" not in html
    assert "<noscript>" in html  # ohne JavaScript steht ein Hinweis da, nicht eine leere Seite


@pytest.mark.parametrize("skript", JS, ids=lambda p: p.name)
def test_skript_setzt_text_nie_als_html_und_nutzt_keinen_speicher(skript):
    code = _text(skript)
    ohne_kommentare = re.sub(r"//[^\n]*|/\*.*?\*/", "", code, flags=re.S)
    verboten = {
        r"innerHTML": "innerHTML", r"outerHTML": "outerHTML", r"insertAdjacent": "insertAdjacentHTML", r"document\.write": "document.write",
        r"\beval\s*\(": "eval", r"new\s+Function": "new Function", r"\bFunction\s*\(": "Function(", r"setTimeout\s*\(\s*[\"'`]": "setTimeout mit Text",
        r"setInterval\s*\(\s*[\"'`]": "setInterval mit Text", r"localStorage": "localStorage", r"sessionStorage": "sessionStorage",
        r"document\.cookie": "Cookie", r"indexedDB": "indexedDB", r"createContextualFragment": "createContextualFragment", r"DOMParser": "DOMParser",
        r"srcdoc": "srcdoc", r"javascript:": "javascript:", r"setAttribute\s*\(\s*[\"']style": "Stil als Attribut", r"\.cssText": "cssText",
        r"XMLHttpRequest": "XMLHttpRequest", r"WebSocket": "WebSocket", r"EventSource": "EventSource", r"window\.open": "window.open",
        r"location\s*=[^=]": "Umleitung", r"location\.(href|assign|replace)\s*=?": "Umleitung", r"document\.domain": "document.domain",
        r"postMessage": "postMessage", r"importScripts": "importScripts", r"navigator\.sendBeacon": "sendBeacon",
    }
    for muster, name in verboten.items():
        assert not re.search(muster, ohne_kommentare), f"{skript.name}: {name}"
    assert not re.search(r"https?://|//[a-z0-9.-]+\.[a-z]{2,}/", ohne_kommentare), "keine fremde Adresse"
    assert not re.search(r"\bimport\s*\(", ohne_kommentare), "kein dynamischer Import"
    for ziel in re.findall(r"""from\s+["']([^"']+)["']""", ohne_kommentare):
        assert re.fullmatch(r"\./[a-z0-9-]+\.js", ziel), f"{skript.name}: Import {ziel}"


@pytest.mark.parametrize("skript", [p for p in JS if p.name in ("board.js", "betreuer.js")], ids=lambda p: p.name)
def test_token_kommt_nur_aus_dem_fragment_nie_aus_der_abfrage_und_geht_nie_in_eine_adresse(skript):
    code = re.sub(r"//[^\n]*|/\*.*?\*/", "", _text(skript), flags=re.S)
    assert "location.hash" in code and "history.replaceState" in code
    for verboten in ("location.search", "URLSearchParams", "document.referrer", "window.name", "document.URL", "location.href"):
        assert verboten not in code, verboten
    for erstes in re.findall(r"fetch\(\s*([^,)]+)", code):  # das erste Argument von fetch: die Adresse
        assert "?" not in erstes and "token" not in erstes.lower() and "hash" not in erstes.lower() and "+" not in erstes, erstes


def test_dom_helfer_laesst_keine_gefaehrlichen_attribute_zu():
    code = re.sub(r"//[^\n]*", "", _text(WEB / "dom.js"))  # ohne Kommentare
    assert "textContent" in code and "createTextNode" in code and "innerHTML" not in code
    assert re.search(r"ERLAUBT\s*=\s*/\^\(", code) and "TypeError" in code, "Attribute nur aus einer festen Liste, alles andere wirft"
    assert not re.search(r"\|on\||\|style\||\|href\||\|src\||\|srcdoc\|", code), "Ereignisse, Stil und Adressen stehen nicht in der Liste"


def test_keine_token_und_keine_platzdaten_in_den_dateien():
    seed_texte = [t for zeile in SEED for t in (zeile[4], zeile[5]) if len(t) > 8]
    for datei in DATEIEN:
        text = _text(datei)
        assert not re.search(r"(?<![0-9a-fA-F])[0-9a-f]{32}(?![0-9a-fA-F])", text), f"{datei.name}: 32 Hexzeichen (ein Platz-Token?)"
        assert not re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", text), f"{datei.name}: eine Kennung"
        assert not re.search(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}", text), f"{datei.name}: ein Bearer mit Wert"
        for geheim in (KERN_TOKEN, BETREUER_TOKEN, *seed_texte):
            assert geheim not in text, f"{datei.name}: {geheim[:12]}"


@pytest.mark.parametrize("stil", CSS, ids=lambda p: p.name)
def test_stil_nutzt_nur_die_palette_ohne_blau_und_keine_fremden_quellen(stil):
    css = _text(stil)
    farben = {f.upper() for f in re.findall(r"#[0-9a-fA-F]{3,8}\b", css)}
    assert farben and farben <= PALETTE, farben - PALETTE
    assert not re.search(r"\b(rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color)\s*\(", css, re.I), "Farben nur als Hexwert der Palette"
    assert not re.search(r"@import|url\s*\(\s*[\"']?https?:|expression\s*\(|behavior\s*:", css, re.I)
    assert re.search(r'font-family:[^;]*"Arial Narrow"[^;]*Arial[^;]*sans-serif', css), "Arial Narrow mit Rückfall auf Arial und sans-serif"
    assert not re.search(r"@font-face", css), "keine externen oder eingebetteten Schriften"
    assert ":focus-visible" in css and "outline" in css, "sichtbarer Fokusring"


def _luminanz(hexwert: str) -> float:
    kanaele = [int(hexwert[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [k / 12.92 if k <= 0.03928 else ((k + 0.055) / 1.055) ** 2.4 for k in kanaele]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _kontrast(a: str, b: str) -> float:
    hell, dunkel = sorted((_luminanz(a), _luminanz(b)), reverse=True)
    return (hell + 0.05) / (dunkel + 0.05)


@pytest.mark.parametrize("stil", CSS, ids=lambda p: p.name)
def test_jedes_farbpaar_im_stil_hat_mindestens_4_5_zu_1(stil):
    """Der Stil nennt jedes Paar aus Schrift und Grund als Kommentar der Form /* Paar: #AAAAAA auf #BBBBBB */ (vor der Regel, die es nutzt)."""
    css = _text(stil)
    paare = re.findall(r"/\*\s*Paar:\s*(#[0-9A-Fa-f]{6})\s+auf\s+(#[0-9A-Fa-f]{6})", css)
    assert len(paare) >= 8, "die Paare sind im Stil nachzulesen"
    for schrift, grund in paare:
        assert _kontrast(schrift, grund) >= 4.5, f"{schrift} auf {grund}: {_kontrast(schrift, grund):.2f}"
    # Rot als Schrift auf Weiß hat nur 4,27 zu 1: Rot kommt nur als Fläche, Linie oder Akzent vor, nie als Textfarbe
    assert not re.search(r"(?<![-a-z])color\s*:\s*(#F40E0E|var\(--rot\))", css, re.I)


def test_kontrastrechnung_stimmt():
    assert round(_kontrast("#000000", "#FFFFFF"), 1) == 21.0 and round(_kontrast("#F40E0E", "#FFFFFF"), 2) == 4.27
    assert _kontrast("#000000", "#F40E0E") >= 4.5 and _kontrast("#6B6B6B", "#F3F3F3") >= 4.5
