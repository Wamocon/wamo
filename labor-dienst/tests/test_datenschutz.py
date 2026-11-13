"""Seite /datenschutz: Der Hinweistext für die Besucher steht nur im Dokument docs/labor-datenschutz-entwurf.md (Abschnitt 1) und wird hier gegen die Seite geprüft,
damit er nicht von Hand zweimal gepflegt wird. Dazu: Version, Knopf Drucken, Link von der Betreuer-Seite, gleiche Schutzköpfe wie die anderen Seiten."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from wamo_labor_dienst import auslieferung

WURZEL = Path(__file__).resolve().parents[2]
DOKUMENT = WURZEL / "docs" / "labor-datenschutz-entwurf.md"
WEB = Path(auslieferung.__file__).resolve().parent / "web"
SEITE = WEB / "datenschutz.html"


def _normal(text: str) -> str:
    """Markdown-Zeichen (**, Rückstriche) und Leerraum vereinheitlichen, damit Dokument und Seite vergleichbar sind."""
    return re.sub(r"\s+", " ", text.replace("**", "").replace("`", "")).strip()


def _abschnitt_1() -> str:
    text = DOKUMENT.read_text(encoding="utf-8")
    treffer = re.search(r"^## 1\. Text für die Besucher[^\n]*\n(.*?)(?=^## )", text, re.S | re.M)
    assert treffer, "Abschnitt 1 im Dokument"
    return treffer.group(1)


def _bloecke() -> dict:
    """Die Teile des Textes aus dem Dokument: Überschrift, Einleitung, Aufzählungspunkte, Verantwortlich, Einwilligung."""
    teile = [t.strip() for t in re.split(r"\n\s*\n", _abschnitt_1().strip())]
    ueberschrift, einleitung, liste, *rest = teile
    punkte = [z[2:].strip() for z in liste.splitlines() if z.startswith("- ")]
    return {"ueberschrift": _normal(ueberschrift), "einleitung": _normal(einleitung), "punkte": [_normal(p) for p in punkte],
            "verantwortlich": next(_normal(r) for r in rest if r.startswith("**Verantwortlich")),
            "einwilligung": next(_normal(r) for r in rest if r.startswith("**Einwilligung"))}


class _Sichtbar(HTMLParser):
    """Sammelt den sichtbaren Text der Seite (ohne Skripte, Stil und noscript) und die Elemente, auf die der Test achtet."""

    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self.tags: list[tuple[str, dict]] = []
        self._aus = 0
        self._stack: list[str] = []
        self.listenpunkte: list[str] = []
        self.ueberschriften: dict[str, list[str]] = {}
        self._li: list[str] | None = None
        self._h: tuple[str, list[str]] | None = None

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag in ("script", "style", "noscript", "title"):
            self._aus += 1
        if tag == "li":
            self._li = []
        if tag in ("h1", "h2", "h3"):
            self._h = (tag, [])

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "title"):
            self._aus -= 1
        if tag == "li" and self._li is not None:
            self.listenpunkte.append(_normal("".join(self._li)))
            self._li = None
        if tag in ("h1", "h2", "h3") and self._h:
            self.ueberschriften.setdefault(self._h[0], []).append(_normal("".join(self._h[1])))
            self._h = None

    def handle_data(self, data):
        if self._aus:
            return
        self.text.append(data)
        if self._li is not None:
            self._li.append(data)
        if self._h is not None:
            self._h[1].append(data)


def _seite() -> _Sichtbar:
    p = _Sichtbar()
    p.feed(SEITE.read_text(encoding="utf-8"))
    return p


def test_dokument_hat_den_erwarteten_aufbau():
    b = _bloecke()
    assert len(b["punkte"]) == 6 and b["verantwortlich"].startswith("Verantwortlich:") and b["einwilligung"].startswith("Einwilligung (Version ")


def test_altersangabe_ab_16_ohne_harte_grenze():
    """Entscheidung von Niko (13.11.): Richtwert mindestens 16 Jahre, keine harte Grenze. Jüngere nehmen mit Zustimmung einer
    Aufsichtsperson teil (so verlangt es auch Art. 8 DSGVO für die Einwilligung)."""
    einwilligung = _bloecke()["einwilligung"]
    assert "mindestens 16 Jahre" in einwilligung and "Aufsichtsperson" in einwilligung and "18" not in einwilligung
    sichtbar = _normal("".join(_seite().text))
    assert "mindestens 16 Jahre" in sichtbar and "18 Jahre" not in sichtbar


def test_jeder_punkt_des_dokuments_steht_in_der_seite():
    sichtbar = _normal("".join(_seite().text))
    b = _bloecke()
    assert b["ueberschrift"] in sichtbar and b["einleitung"] in sichtbar
    for punkt in b["punkte"]:
        assert punkt in sichtbar, punkt[:50]
    assert b["verantwortlich"] in sichtbar and b["einwilligung"] in sichtbar


def test_die_seite_zeigt_genau_sechs_aufzaehlungspunkte_in_der_reihenfolge_des_dokuments():
    assert _seite().listenpunkte == _bloecke()["punkte"]


def test_ueberschrift_ist_die_hauptueberschrift_der_seite():
    assert _seite().ueberschriften["h1"] == [_bloecke()["ueberschrift"]]


def test_der_sichtbare_text_ist_genau_der_text_des_dokuments_plus_kopfzeile_und_knopf():
    """Strenger als die Einzelprüfungen: Die Seite zeigt den Abschnitt 1 zusammenhängend und nichts darüber hinaus außer Kopfzeile und Knopf."""
    erwartet = _normal(re.sub(r"(?m)^- ", "", _abschnitt_1()))
    sichtbar = _normal("".join(_seite().text))
    anfang = sichtbar.find(erwartet)
    assert anfang >= 0, "der Text des Dokuments steht nicht zusammenhängend in der Seite"
    rest = sichtbar[:anfang] + " " + sichtbar[anfang + len(erwartet):]
    assert set(rest.split()) <= {"WAMOCON", "Test-Jira", "Datenschutzhinweis", "Drucken"}, rest


def test_version_der_seite_der_betreuer_seite_und_des_dokuments_stimmen_ueberein():
    konstante = re.search(r"export const DATENSCHUTZ_VERSION\s*=\s*(\d+);", (WEB / "datenschutz-logik.js").read_text(encoding="utf-8"))
    assert konstante, "DATENSCHUTZ_VERSION in datenschutz-logik.js"
    version = int(konstante.group(1))
    seite = re.search(r"Einwilligung \(Version (\d+)\)", _normal("".join(_seite().text)))
    dokument = re.search(r"Einwilligung \(Version (\d+)\)", _abschnitt_1().replace("**", ""))
    kopf = re.search(r"Dieser Text hat die \*\*Version (\d+)\*\*", DOKUMENT.read_text(encoding="utf-8"))
    assert seite and dokument and kopf
    assert int(seite.group(1)) == int(dokument.group(1)) == int(kopf.group(1)) == version


def test_die_seite_ist_gross_gesetzt_und_druckbar():
    css = (WEB / "stil.css").read_text(encoding="utf-8")
    regel = re.search(r"\.datenschutz-text\s*\{[^}]*font-size:\s*([0-9.]+)rem", css)
    assert regel and float(regel.group(1)) >= 1.35, "große Schrift für den Hinweis"
    assert "@media print" in css and ".nicht-drucken" in css


def test_knopf_drucken_ohne_inline_skript():
    html = SEITE.read_text(encoding="utf-8")
    assert re.search(r'<button type="button" class="knopf knopf-hell kopf-knopf nicht-drucken" id="drucken">Drucken</button>', html)
    assert re.search(r"<script type=\"module\" src=\"/web/datenschutz\.js\"></script>", html)
    js = (WEB / "datenschutz.js").read_text(encoding="utf-8")
    assert "window.print()" in js and '"drucken"' in js and "addEventListener" in js
    assert not re.search(r"\bonclick\b|javascript:", html + js)


def test_seite_hat_keine_links_und_keine_fremden_adressen():
    p = _seite()
    assert not [t for t, _ in p.tags if t in ("a", "form", "iframe", "img", "object")]
    html = SEITE.read_text(encoding="utf-8")
    assert not re.search(r"https?://|<a[ >]|<form[ >]", html)  # die Adressen im Text (wamocon.com/datenschutz) sind nur Text, kein Link


@pytest.mark.parametrize("adresse", ["/datenschutz", "/web/datenschutz.js", "/web/datenschutz-logik.js"])
def test_seite_und_dateien_haben_dieselben_schutzkoepfe_wie_das_board(client, adresse):
    r, board = client.get(adresse), client.get("/board")
    assert r.status_code == 200
    for kopf in ("content-security-policy", "x-content-type-options", "referrer-policy", "cache-control", "x-frame-options", "permissions-policy"):
        assert r.headers[kopf] == board.headers[kopf], kopf


def test_seite_ist_oeffentlich_ohne_token_und_nur_get(client):
    r = client.get("/datenschutz")
    assert r.status_code == 200 and r.headers["content-type"] == "text/html; charset=utf-8" and r.content == SEITE.read_bytes()
    for methode in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
        assert client.request(methode, "/datenschutz").status_code == 405
    for pfad in ("/datenschutz/", "/Datenschutz", "/DATENSCHUTZ", "/datenschutz.html", "/datenschutz/x", "/web/datenschutz.html"):
        assert client.get(pfad).status_code == 404, pfad


def test_betreuer_seite_verlinkt_den_hinweis_in_neuem_tab_ohne_opener():
    html = (WEB / "betreuer.html").read_text(encoding="utf-8")
    anker = re.findall(r"<a\b[^>]*>[^<]*</a>", html)
    assert len(anker) == 1
    assert 'href="/datenschutz"' in anker[0] and 'target="_blank"' in anker[0] and 'rel="noopener noreferrer"' in anker[0]
    assert "Hinweis für den Besucher anzeigen" in anker[0]


def test_seiten_enthalten_keine_geheimnisse_auch_die_neue_nicht(client):
    from hilfen import BETREUER_TOKEN, KERN_TOKEN

    for adresse in ("/datenschutz", "/web/datenschutz.js", "/web/datenschutz-logik.js"):
        text = client.get(adresse).text
        assert KERN_TOKEN not in text and BETREUER_TOKEN not in text and not re.search(r"[0-9a-f]{32}", text)


def _code(name: str) -> str:
    return re.sub(r"//[^" + chr(10) + r"]*", "", (WEB / name).read_text(encoding="utf-8"))


def _dialog() -> str:
    js = _code("betreuer.js")
    return js.split("function fensterOeffnen")[1].split("function start")[0]


def test_betreuer_dialog_hat_pflicht_checkbox_und_sendet_die_version_aus_der_konstante():
    js, dialog = _code("betreuer.js"), _dialog()
    assert re.search(r'from "\./datenschutz-logik\.js"', js) and "einwilligungKoerper()" in dialog
    assert 'type: "checkbox"' in dialog and "BESTAETIGUNGSTEXT" in dialog and "neuerDurchlaufFreigabe(" in dialog and "required" in dialog
    assert 'verweis("Hinweis für den Besucher anzeigen", "/datenschutz")' in dialog
    assert "DATENSCHUTZ_VERSION" in dialog, "die Version wird nur lesend angezeigt"


def test_die_version_ist_im_dialog_nicht_mehr_frei_editierbar():
    js = _code("betreuer.js")
    assert "einwilligung-eingabe" not in js and "einwilligungPruefen" not in js and "Math.max" not in js
    assert "einwilligung_version" not in js, "den Körper liefert allein einwilligungKoerper() aus datenschutz-logik.js"
    assert not re.search(r"\{\s*einwilligung_version\s*:", (WEB / "betreuer-logik.js").read_text(encoding="utf-8"))


def test_nur_der_dialog_neuer_durchlauf_zeigt_haken_und_version():
    dialog = _dialog()
    assert dialog.count('type: "checkbox"') == 1
    assert 'a.braucht === "einwilligung"' in dialog, "Haken und Version nur dort, wo die Funktion eine Einwilligung braucht"
    assert "versionVeraltet(hoechste)" in dialog, "ist beim Dienst schon eine höhere Version erfasst, bleibt der Knopf gesperrt"


def test_der_haken_hat_ein_sichtbares_etikett_und_grosse_flaeche():
    css = (WEB / "stil.css").read_text(encoding="utf-8")
    regel = re.search(r"\.haken\s*\{[^}]*\}", css)
    assert regel and "cursor" not in regel.group(0)
    assert re.search(r"\.haken input\s*\{[^}]*width:\s*([0-9.]+)rem", css), "große Haken-Fläche"
