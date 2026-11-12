"""Tests für scripts/wamo_labor_abnahme.py (Abnahme des Test-Jira-Dienstes gegen den Vertrag, docs/labor-dienst-vertrag.md Abschnitt 7).

Aufruf (aus scripts, mit der Umgebung von voice-core):
  D:/IDEA/Projekt/wamo/voice-core/.venv/Scripts/python.exe -m pytest -p no:cacheprovider -q test_wamo_labor_abnahme.py
Kein Netz und kein Server: Die Attrappe läuft im Prozess (httpx.ASGITransport), die Uhr ist eine falsche Uhr, die Wartezeiten der
Schreibrate kosten nichts. Das Skript gilt erst als Prüfmittel, wenn es gegen die saubere Nachbildung besteht (grün) und gegen jede
absichtlich verwundbare Variante durchfällt (rot), und zwar an der Anforderung, die der Fehler bricht.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest import mock

import certifi
import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor_abnahme as ab  # noqa: E402
import wamo_labor_abnahme_basis as basis  # noqa: E402
import wamo_labor_abnahme_pruefungen as pr  # noqa: E402
import wamo_labor_abnahme_verwundbar as vw  # noqa: E402

import labor_attrappe  # noqa: E402,I001 (liegt in voice-core, der Pfad kommt aus wamo_labor_abnahme_verwundbar)

SKRIPT = Path(ab.__file__)
WURZEL = SKRIPT.parent.parent
AUTOMATISCH = ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A11")
TOKEN = "T" + "k9-Z_x.~+=" * 4 + "abcdefgh"  # 49 Zeichen, so wie ein echter Token des Kerns aussehen darf


def lauf(defekte=(), **kw):
    with vw.starten(defekte=defekte) as ziel:
        return ab.pruefe_ziel(ziel, **kw)


# ---------- Prüfmittel: grün gegen die saubere Nachbildung, rot gegen die verwundbare ----------
@pytest.fixture(scope="module")
def sauber():
    return lauf()


@pytest.fixture(scope="module")
def alles_kaputt():
    return lauf(defekte=sorted(vw.DEFEKTE))


def test_saubere_nachbildung_besteht_jede_pruefung(sauber):
    assert sauber.abbruch is None
    for nr in AUTOMATISCH:
        assert sauber.nach_nr[nr].status == basis.BESTANDEN, f"{nr}: {sauber.nach_nr[nr].meldung}"
    assert sauber.nach_nr["A10"].status == basis.VON_HAND  # Netz: nur von Hand am Server
    assert ab.exit_code(sauber) == ab.EXIT_OK


def test_verwundbare_variante_faellt_an_jeder_anforderung_durch(alles_kaputt):
    for nr in AUTOMATISCH:
        assert alles_kaputt.nach_nr[nr].status == basis.DURCHGEFALLEN, f"{nr} blieb grün: {alles_kaputt.nach_nr[nr].meldung}"
    assert ab.exit_code(alles_kaputt) == ab.EXIT_DURCHGEFALLEN


def test_jede_automatische_anforderung_hat_mindestens_einen_fehlerfall():
    assert set(AUTOMATISCH) <= {d.anforderung for d in vw.DEFEKTE.values()}


@pytest.mark.parametrize("name", sorted(vw.DEFEKTE))
def test_jeder_defekt_wird_an_seiner_anforderung_erkannt(name):
    defekt = vw.DEFEKTE[name]
    ergebnis = lauf(defekte=(name,))
    assert ergebnis.nach_nr[defekt.anforderung].status == basis.DURCHGEFALLEN, f"{name}: {defekt.anforderung} blieb grün"
    zu_unrecht = {nr for nr in AUTOMATISCH if ergebnis.nach_nr[nr].status == basis.DURCHGEFALLEN} - {defekt.anforderung} - set(defekt.neben)
    assert not zu_unrecht, f"{name} macht auch {sorted(zu_unrecht)} rot"


def test_verwundbare_variante_liegt_nicht_im_kern():
    for datei in (WURZEL / "voice-core").glob("*.py"):
        quelle = datei.read_text(encoding="utf-8")
        assert "wamo_labor_abnahme" not in quelle, datei.name


# ---------- Token ----------
def test_token_wird_nie_ausgegeben_bei_der_attrappe(monkeypatch, capsys):
    monkeypatch.setattr(secrets, "token_urlsafe", lambda n=32: TOKEN)
    assert ab.main(["--attrappe", "--auswahl", "A1,A9"], env={}) == ab.EXIT_TEILLAUF
    ausgabe = capsys.readouterr()
    assert TOKEN not in ausgabe.out + ausgabe.err


def test_ausgabe_ersetzt_den_token_auch_in_meldungen(monkeypatch, capsys):
    with vw.starten() as ziel:
        def a_token(_z, _o):
            return basis.Ergebnis("A1", "XSS", basis.DURCHGEFALLEN, f"Dienst echot Bearer {ziel.token} zurück", von_hand=(f"Rest mit {ziel.token}",))

        monkeypatch.setitem(pr.PRUEFUNGEN, "A1", ("XSS", a_token))
        code = ab.main(["https://dienst.test", "--ja", "--auswahl", "A1"], env={"WAMO_LABOR_TOKEN": ziel.token}, transport=ziel.transport, takt=ziel.takt)
    ausgabe = capsys.readouterr().out
    assert code == ab.EXIT_DURCHGEFALLEN and ziel.token not in ausgabe and "Bearer [Token] zurück" in ausgabe and "Rest mit [Token]" in ausgabe
    assert ab.bereinigen(f"Bearer {TOKEN}", [TOKEN]) == "Bearer [Token]"
    assert ab.bereinigen("ohne Geheimnis", [TOKEN, ""]) == "ohne Geheimnis"


def test_token_geht_nicht_als_argument(capsys):
    with pytest.raises(SystemExit) as e:
        ab.main(["https://dienst.test", "--token", TOKEN], env={})
    assert e.value.code == 2
    assert TOKEN not in capsys.readouterr().err


def test_token_fehlt(capsys):
    assert ab.main(["https://dienst.test"], env={}) == ab.EXIT_AUFRUF
    assert "WAMO_LABOR_TOKEN" in capsys.readouterr().err


def test_unbrauchbarer_token_wird_nicht_genannt(capsys):
    falsch = "GEHEIM#" + "x" * 40
    assert ab.main(["https://dienst.test"], env={"WAMO_LABOR_TOKEN": falsch}) == ab.EXIT_AUFRUF
    ausgabe = capsys.readouterr()
    assert "GEHEIM" not in ausgabe.out + ausgabe.err and "unbrauchbar" in ausgabe.err


def test_token_aus_datei(tmp_path, capsys):
    with vw.starten() as ziel:
        datei = tmp_path / "token.txt"
        datei.write_text(ziel.token + "\n", encoding="utf-8")
        code = ab.main(["https://dienst.test", "--ja", "--token-datei", str(datei), "--auswahl", "A9"], env={}, transport=ziel.transport, takt=ziel.takt)
    assert code == ab.EXIT_TEILLAUF
    assert ziel.token not in capsys.readouterr().out


def test_token_datei_mit_bom_wird_gelesen(tmp_path):
    with vw.starten() as ziel:
        datei = tmp_path / "token-bom.txt"
        datei.write_bytes(b"\xef\xbb\xbf" + ziel.token.encode() + b"\r\n")  # so schreibt Notepad
        code = ab.main(["https://dienst.test", "--ja", "--token-datei", str(datei), "--auswahl", "A9"], env={}, transport=ziel.transport, takt=ziel.takt)
    assert code == ab.EXIT_TEILLAUF


@pytest.mark.parametrize("kodierung", ["utf-16", "latin-1"])
def test_token_datei_in_falscher_kodierung_endet_mit_klarer_meldung(tmp_path, capsys, kodierung):
    datei = tmp_path / "token.txt"
    datei.write_text("Tür " + TOKEN, encoding=kodierung)  # PowerShell 5 schreibt mit > UTF-16, ältere Editoren Latin-1
    assert ab.main(["https://dienst.test", "--ja", "--token-datei", str(datei)], env={}) == ab.EXIT_AUFRUF
    ausgabe = capsys.readouterr()
    assert "Token-Datei" in ausgabe.err and "UTF-8" in ausgabe.err and TOKEN not in ausgabe.out + ausgabe.err


def test_token_datei_fehlt(tmp_path, capsys):
    assert ab.main(["https://dienst.test", "--token-datei", str(tmp_path / "gibt-es-nicht")], env={}) == ab.EXIT_AUFRUF
    assert "Token-Datei" in capsys.readouterr().err


# ---------- Adresse, Erreichbarkeit, Zeit ----------
@pytest.mark.parametrize("adresse", ["dienst.test", "ftp://dienst.test", "https://nutzer:geheimpw@dienst.test", "https://dienst.test/pfad",
                                     "https://dienst.test?a=1", "http://dienst.test", ""])
def test_falsche_adresse_endet_mit_klarer_meldung(adresse, capsys):
    assert ab.main([adresse], env={"WAMO_LABOR_TOKEN": TOKEN}) == ab.EXIT_AUFRUF
    ausgabe = capsys.readouterr()
    assert "Adresse" in ausgabe.err and "geheimpw" not in ausgabe.out + ausgabe.err and TOKEN not in ausgabe.err


def test_board_adresse_und_platz_token_datei_werden_geprueft(tmp_path, capsys):
    assert ab.main(["--attrappe", "--board-url", "ftp://dienst.test/board"], env={}) == ab.EXIT_AUFRUF
    assert ab.main(["--attrappe", "--board-url", "https://nutzer:geheimpw@dienst.test/board"], env={}) == ab.EXIT_AUFRUF
    assert ab.main(["--attrappe", "--platz-token-datei", str(tmp_path / "fehlt")], env={}) == ab.EXIT_AUFRUF
    assert ab.main(["--attrappe"], env={"WAMO_LABOR_PLATZ_TOKEN": "zu-kurz"}) == ab.EXIT_AUFRUF
    ausgabe = capsys.readouterr()
    assert "geheimpw" not in ausgabe.out + ausgabe.err and "zu-kurz" not in ausgabe.out + ausgabe.err
    assert ab.adresse_pruefen("https://dienst.test:8443/board?a=1", mit_pfad=True) == "https://dienst.test:8443/board?a=1"


def test_dienst_nicht_erreichbar(capsys):
    def abgelehnt(_anfrage):
        raise httpx.ConnectError("abgelehnt")

    code = ab.main(["https://dienst.test:8443", "--ja"], env={"WAMO_LABOR_TOKEN": TOKEN}, transport=httpx.MockTransport(abgelehnt))
    ausgabe = capsys.readouterr()
    assert code == ab.EXIT_AUFRUF and "nicht erreichbar" in ausgabe.err and "https://dienst.test:8443" in ausgabe.err
    assert TOKEN not in ausgabe.out + ausgabe.err


def test_geschlossener_port_auf_loopback(capsys):
    assert ab.main(["http://127.0.0.1:1", "--ja", "--frist", "4"], env={"WAMO_LABOR_TOKEN": TOKEN}) == ab.EXIT_AUFRUF
    assert "nicht erreichbar" in capsys.readouterr().err


def test_zeitueberschreitung_endet_mit_klarer_meldung(capsys):
    async def langsam(scope, receive, send):
        await asyncio.sleep(30)

    code = ab.main(["https://dienst.test", "--ja", "--frist", "0.2"], env={"WAMO_LABOR_TOKEN": TOKEN}, transport=httpx.ASGITransport(app=langsam))
    ausgabe = capsys.readouterr()
    assert code == ab.EXIT_AUFRUF and "Zeitüberschreitung" in ausgabe.err and "0.2" in ausgabe.err
    assert TOKEN not in ausgabe.out + ausgabe.err


def test_dienst_antwortet_nicht_wie_im_vertrag(capsys):
    async def html(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/html")]})
        await send({"type": "http.response.body", "body": b"<html>Willkommen</html>"})

    code = ab.main(["https://dienst.test", "--ja"], env={"WAMO_LABOR_TOKEN": TOKEN}, transport=httpx.ASGITransport(app=html))
    assert code == ab.EXIT_AUFRUF and "V1" in capsys.readouterr().err


def test_ausfall_mitten_im_lauf_bricht_ab_und_zeigt_die_tabelle(capsys):
    zaehler = {"n": 0}

    def stirbt(anfrage):
        zaehler["n"] += 1
        if zaehler["n"] <= 4:  # V1 und V2 je Platz laufen noch
            return httpx.Response(200, json={"durchlauf_id": "5b0f0c1e-7a1d-4c61-9b52-0f6d2a8e41aa", "offen": True, "einwilligung_version": 1,
                                             "einwilligung_zeit": None, "tickets": [], "gesamt": 0})
        raise httpx.ConnectError("weg")

    code = ab.main(["https://dienst.test", "--ja", "--auswahl", "A1"], env={"WAMO_LABOR_TOKEN": TOKEN}, transport=httpx.MockTransport(stirbt))
    ausgabe = capsys.readouterr()
    assert code == ab.EXIT_AUFRUF and "antwortet nicht mehr" in ausgabe.err


# ---------- Ausgangsstand der Plätze ----------
def test_platz_ohne_offenen_durchlauf_wird_nicht_geprueft(capsys):
    with vw.starten() as ziel:
        ziel.attrappe.durchlauf_beenden(15)
        code = ab.main(["https://dienst.test", "--ja"], env={"WAMO_LABOR_TOKEN": ziel.token}, transport=ziel.transport, takt=ziel.takt)
    ausgabe = capsys.readouterr()
    assert code == ab.EXIT_AUFRUF and "Platz 15" in ausgabe.err and "Durchlauf" in ausgabe.err


def test_benutzter_platz_verlangt_einen_neuen_durchlauf(capsys):
    with vw.starten() as ziel:
        erst = ab.main(["https://dienst.test", "--ja", "--auswahl", "A1"], env={"WAMO_LABOR_TOKEN": ziel.token}, transport=ziel.transport, takt=ziel.takt)
        zweit = ab.main(["https://dienst.test", "--ja", "--auswahl", "A1"], env={"WAMO_LABOR_TOKEN": ziel.token}, transport=ziel.transport, takt=ziel.takt)
        ziel.attrappe.neuer_durchlauf(15)
        ziel.attrappe.neuer_durchlauf(16)
        wieder = ab.main(["https://dienst.test", "--ja", "--auswahl", "A1"], env={"WAMO_LABOR_TOKEN": ziel.token}, transport=ziel.transport, takt=ziel.takt)
    assert (erst, zweit, wieder) == (ab.EXIT_TEILLAUF, ab.EXIT_AUFRUF, ab.EXIT_TEILLAUF)
    assert "neuen Durchlauf" in capsys.readouterr().err


# ---------- Aufruf ----------
def test_auswahl_ist_ein_teillauf_und_nie_gruen(capsys):
    assert ab.main(["--attrappe", "--auswahl", "A9"], env={}) == ab.EXIT_TEILLAUF
    ausgabe = capsys.readouterr().out
    assert "Teillauf" in ausgabe and "nicht gelaufen" in ausgabe and "A9" in ausgabe


def test_unbekannte_auswahl(capsys):
    assert ab.main(["--attrappe", "--auswahl", "A99"], env={}) == ab.EXIT_AUFRUF
    assert "A99" in capsys.readouterr().err


def test_trocken_sendet_nichts(capsys):
    def verboten(_anfrage):
        raise AssertionError("Trockenlauf darf nichts senden")

    code = ab.main(["https://dienst.test", "--trocken"], env={"WAMO_LABOR_TOKEN": TOKEN}, transport=httpx.MockTransport(verboten))
    ausgabe = capsys.readouterr().out
    assert code == ab.EXIT_OK and "keine Anfrage" in ausgabe and TOKEN not in ausgabe
    assert all(nr in ausgabe for nr in ("A1", "A4", "A10", "A11")) and "Platz 15" in ausgabe and "Platz 16" in ausgabe


def test_attrappe_und_adresse_schliessen_sich_aus(capsys):
    assert ab.main(["https://dienst.test", "--attrappe"], env={}) == ab.EXIT_AUFRUF
    assert ab.main(["--verwundbar", "--attrappe"], env={}) == ab.EXIT_AUFRUF
    assert ab.main([], env={}) == ab.EXIT_AUFRUF


def test_verwundbar_mit_einem_einzelnen_defekt(capsys):
    assert ab.main(["--verwundbar", "--defekt", "sql", "--auswahl", "A2"], env={}) == ab.EXIT_DURCHGEFALLEN
    assert "durchgefallen" in capsys.readouterr().out
    assert ab.main(["--verwundbar", "--defekt", "gibt-es-nicht"], env={}) == ab.EXIT_AUFRUF


def test_lauf_beruehrt_nur_die_abnahmeplaetze():
    """Jede Anfrage trägt den Platzkopf 15 oder 16. Ausnahme sind Proben mit ungültigem Platz (99, Einspritzungen, kein Kopf), die der Dienst
    mit 404 ablehnen muss; ein Platz von 1 bis 14 kommt nie vor, und jede schreibende Anfrage hat genau Platz 15, 16 oder eine ungültige Kennung."""
    gesehen: set[tuple[str, str]] = set()
    with vw.starten() as ziel:
        async def app(scope, receive, send):
            if scope["type"] == "http":
                gesehen.add((scope["method"], dict(scope["headers"]).get(b"x-wamo-platz", b"(keiner)").decode()))
            await ziel.app(scope, receive, send)

        ab.pruefe_ziel(ziel, transport=httpx.ASGITransport(app=app))
    plaetze = {platz for _methode, platz in gesehen}
    assert {"15", "16"} <= plaetze
    assert not plaetze & {str(n) for n in range(1, 15)}, sorted(plaetze)  # nie ein Besucherplatz
    schreibend = {platz for methode, platz in gesehen if methode != "GET"}
    assert all(platz in ("15", "16") or not re.fullmatch(r"[0-9]{1,2}", platz) or int(platz) > 16 for platz in schreibend), sorted(schreibend)


def test_trocken_gilt_nur_mit_adresse(capsys):
    assert ab.main(["--attrappe", "--trocken"], env={}) == ab.EXIT_AUFRUF
    assert "--trocken" in capsys.readouterr().err


def test_tabelle_nennt_je_anforderung_das_ergebnis(sauber):
    text = ab.tabelle(sauber, "Attrappe")
    for nr in ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "A11"):
        assert any(zeile.startswith(nr + " ") for zeile in text.splitlines()), nr
    assert "bestanden" in text and "von Hand" in text
    assert text.splitlines()[-1].startswith("Ergebnis: BESTANDEN")


def test_kritische_anforderungen_sperren_den_dienst(alles_kaputt):
    text = ab.tabelle(alles_kaputt, "Attrappe")
    assert "geht nicht an Besucher" in text and "DURCHGEFALLEN" in text


def test_kommandozeile_attrappe_gruen_und_verwundbar_rot():
    gruen = subprocess.run([sys.executable, str(SKRIPT), "--attrappe"], capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert gruen.returncode == 0, gruen.stdout + gruen.stderr
    assert "Ergebnis: BESTANDEN" in gruen.stdout and "A11" in gruen.stdout
    rot = subprocess.run([sys.executable, str(SKRIPT), "--verwundbar"], capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert rot.returncode == 1, rot.stdout + rot.stderr
    assert rot.stdout.count("durchgefallen") >= len(AUTOMATISCH)


# ---------- Teile mit Optionen: Board-Seite (A1) und Platz-Token, Betreuer-Pfade (A6, A9) ----------
def _mit_seiten(ziel, seiten):
    """ASGI-Schicht vor der Nachbildung: feste Seiten (Pfad -> (Status, Köpfe, Inhalt)), alles andere geht an die Nachbildung."""
    async def app(scope, receive, send):
        if scope["type"] == "http" and scope["path"] in seiten:
            status, koepfe, inhalt = seiten[scope["path"]](scope)
            await send({"type": "http.response.start", "status": status, "headers": [(k.encode(), v.encode()) for k, v in koepfe]})
            await send({"type": "http.response.body", "body": inhalt})
            return
        await ziel.app(scope, receive, send)

    return httpx.ASGITransport(app=app)


GUTE_KOEPFE = [("content-type", "text/html; charset=utf-8"), ("x-content-type-options", "nosniff"), ("referrer-policy", "no-referrer"),
               ("content-security-policy", "default-src 'self'; frame-ancestors 'none'")]


def _a1_mit_board(koepfe, inhalt=b"<html><body>Board</body></html>"):
    with vw.starten() as ziel:
        transport = _mit_seiten(ziel, {"/board": lambda _s: (200, koepfe, inhalt)})
        return ab.pruefe_ziel(ziel, auswahl=["A1"], optionen=basis.Optionen(board_url="https://dienst.test/board"), transport=transport)


def test_a1_prueft_die_koepfe_der_board_seite():
    gut = _a1_mit_board(GUTE_KOEPFE).nach_nr["A1"]
    assert gut.status == basis.BESTANDEN, gut.meldung
    ohne_csp = _a1_mit_board([k for k in GUTE_KOEPFE if k[0] != "content-security-policy"]).nach_nr["A1"]
    assert ohne_csp.status == basis.DURCHGEFALLEN and "Content-Security-Policy" in ohne_csp.meldung
    inline = _a1_mit_board([(k, "default-src 'self' 'unsafe-inline'; frame-ancestors 'none'" if k == "content-security-policy" else v)
                            for k, v in GUTE_KOEPFE]).nach_nr["A1"]
    assert inline.status == basis.DURCHGEFALLEN
    # die strengere Fassung der Seiten des Dienstes (default-src 'none' mit eigenen script-src und style-src) besteht, eine CSP ohne default-src nicht
    streng = _a1_mit_board([(k, "default-src 'none'; script-src 'self'; style-src 'self'; frame-ancestors 'none'" if k == "content-security-policy" else v)
                            for k, v in GUTE_KOEPFE]).nach_nr["A1"]
    assert streng.status == basis.BESTANDEN, streng.meldung
    ohne_default = _a1_mit_board([(k, "script-src 'self'; frame-ancestors 'none'" if k == "content-security-policy" else v)
                                  for k, v in GUTE_KOEPFE]).nach_nr["A1"]
    assert ohne_default.status == basis.DURCHGEFALLEN and "default-src" in ohne_default.meldung
    offen = _a1_mit_board([(k, "default-src *; frame-ancestors 'none'" if k == "content-security-policy" else v)
                           for k, v in GUTE_KOEPFE]).nach_nr["A1"]
    assert offen.status == basis.DURCHGEFALLEN
    roh = _a1_mit_board(GUTE_KOEPFE, b"<html><script>alert(1)</script></html>").nach_nr["A1"]
    assert roh.status == basis.BESTANDEN and any("Rohseite" in rest for rest in roh.von_hand)  # ohne Token und Platz zeigt die Seite keine Tickets: nicht prüfbar


def test_board_seite_bekommt_nie_den_bearer_und_keinen_platz():
    gesehen: list[dict] = []

    def seite(scope):
        gesehen.append({k.decode(): v.decode() for k, v in scope["headers"]})
        return 200, GUTE_KOEPFE, b"<html></html>"

    with vw.starten() as ziel:
        transport = _mit_seiten(ziel, {"/board": seite})
        ab.pruefe_ziel(ziel, auswahl=["A1"], optionen=basis.Optionen(board_url="https://dienst.test/board"), transport=transport)
    assert gesehen and all("authorization" not in k and "x-wamo-platz" not in k for k in gesehen)


def test_a1_ohne_board_adresse_nennt_den_rest_von_hand(sauber):
    assert any("Browserlauf" in rest for rest in sauber.nach_nr["A1"].von_hand)


def _a9_mit_betreuer(antworten):
    with vw.starten() as ziel:
        def seite(scope):
            auth = dict(scope["headers"]).get(b"authorization", b"").decode()
            return antworten(auth)

        transport = _mit_seiten(ziel, {"/betreuer/status": seite, "/platz/16": seite})
        optionen = basis.Optionen(platz_token="P" * 40, betreuer_pfade=("GET /betreuer/status",), platz_pfade=("/platz/{platz}",))
        return ab.pruefe_ziel(ziel, auswahl=["A6", "A9"], optionen=optionen, transport=transport)


def test_a9_betreuer_pfad_mit_platz_token_verlangt_403():
    gut = _a9_mit_betreuer(lambda auth: (403 if auth == "Bearer " + "P" * 40 else 401, [("content-type", "application/json")], b"{}"))
    assert gut.nach_nr["A9"].status == basis.BESTANDEN, gut.nach_nr["A9"].meldung
    assert gut.nach_nr["A6"].status == basis.BESTANDEN, gut.nach_nr["A6"].meldung
    offen = _a9_mit_betreuer(lambda auth: (200, [("content-type", "application/json")], b"{}"))
    assert offen.nach_nr["A9"].status == basis.DURCHGEFALLEN and offen.nach_nr["A6"].status == basis.DURCHGEFALLEN


def test_a9_und_a6_ohne_platz_token_nennen_den_rest_von_hand(sauber):
    assert any("Platz-Token" in rest for rest in sauber.nach_nr["A6"].von_hand)
    assert any("Betreuer" in rest for rest in sauber.nach_nr["A9"].von_hand)
    assert any("Datenbankgröße" in rest for rest in sauber.nach_nr["A4"].von_hand)


# ---------- Bausteine ----------
def test_schreibtakt_wartet_auf_das_ende_des_fensters():
    takt = basis.FalscherTakt()
    gebremst = basis.Schreibtakt(takt, grenze=3, fenster=60.0)
    start = takt.jetzt()

    async def lauf_():
        for _ in range(3):
            await gebremst.vor_schreiben()
        assert takt.jetzt() == start  # drei passen ins Fenster
        await gebremst.vor_schreiben()
        return takt.jetzt() - start

    assert asyncio.run(lauf_()) >= 60.0


def test_sammler_nennt_die_ersten_fehler_kurz():
    s = basis.Sammler()
    for i in range(6):
        s.erwarte(False, f"Fehler {i}")
    s.erwarte(True, "gut")
    e = s.ergebnis("A1", "XSS", "alles gut")
    assert e.status == basis.DURCHGEFALLEN and "Fehler 0" in e.meldung and "Fehler 3" not in e.meldung and "3 weitere" in e.meldung
    ok = basis.Sammler()
    ok.erwarte(True, "gut")
    assert ok.ergebnis("A1", "XSS", "1 Prüfung").status == basis.BESTANDEN


def test_antwort_liest_json_und_code():
    a = basis.Antwort(409, {"content-type": "application/json"}, json.dumps({"fehler": "ticket_veraendert", "text": "x"}).encode())
    assert a.code == "ticket_veraendert" and a.json["text"] == "x" and a.ist(409, "ticket_veraendert") and not a.ist(409, "andere")
    kaputt = basis.Antwort(200, {}, b"<html>")
    assert kaputt.json is None and kaputt.code is None
    assert not basis.Antwort(0, {}, b"", fehler="Zeitüberschreitung").ist(200)


# ---------- Review: echter Socket, Pfade, Bestätigung, strengere Quoten, Ausgang, Kodierung, Größe ----------
@contextlib.contextmanager
def echter_socket(defekte=()):
    """Die Nachbildung hinter uvicorn auf Loopback, wie der Kern und die Kollegen den Dienst über h11 erreichen. In-Prozess-Tests mit ASGITransport
    sehen keine Fehler der HTTP-Schicht (zum Beispiel ein ungültiger Kopfwert)."""
    import uvicorn

    with vw.starten(defekte=defekte) as ziel:
        server = uvicorn.Server(uvicorn.Config(ziel.app, host="127.0.0.1", port=0, log_level="error", lifespan="off"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        ende = time.monotonic() + 15
        while not server.started and time.monotonic() < ende:
            time.sleep(0.02)
        assert server.started, "uvicorn startet nicht"
        try:
            yield ziel, f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
        finally:
            server.should_exit = True
            thread.join(15)


def test_a9_ueber_echten_socket(capsys):
    with echter_socket() as (ziel, url):
        code = ab.main([url, "--ja", "--auswahl", "A9"], env={"WAMO_LABOR_TOKEN": ziel.token}, takt=ziel.takt)
    ausgabe = capsys.readouterr()
    assert code == ab.EXIT_TEILLAUF, ausgabe.out + ausgabe.err
    assert re.search(r"^A9\s+Zugang\s+bestanden", ausgabe.out, re.M), ausgabe.out


def test_ganzer_lauf_ueber_echten_socket_bis_exit_0(capsys):
    with echter_socket() as (ziel, url):
        code = ab.main([url, "--ja"], env={"WAMO_LABOR_TOKEN": ziel.token}, takt=ziel.takt)
    ausgabe = capsys.readouterr()
    assert code == ab.EXIT_OK, ausgabe.out + ausgabe.err
    assert ziel.token not in ausgabe.out + ausgabe.err


def test_verwundbar_faellt_auch_ueber_den_echten_socket_durch(capsys):
    with echter_socket(defekte=("bearer", "sql")) as (ziel, url):
        code = ab.main([url, "--ja", "--auswahl", "A2", "A9"], env={"WAMO_LABOR_TOKEN": ziel.token}, takt=ziel.takt)
    assert code == ab.EXIT_DURCHGEFALLEN
    assert capsys.readouterr().out.count("durchgefallen") >= 3


def test_leerer_bearer_sendet_kein_leerzeichen_am_ende():
    """h11 lehnt einen Kopfwert mit Leerzeichen am Ende ab (LocalProtocolError): Der leere Bearer ist "Bearer" ohne Leerzeichen."""
    gesehen: list[str | None] = []

    def handler(anfrage):
        gesehen.append(anfrage.headers.get("authorization"))
        return httpx.Response(401, json={"fehler": "token_ungueltig", "text": "Der Zugang ist nicht gültig."})

    async def lauf_():
        async with httpx.AsyncClient(base_url="https://dienst.test", transport=httpx.MockTransport(handler)) as c:
            await basis.Zugang(c, TOKEN, basis.FalscherTakt()).v1(15, bearer="")

    asyncio.run(lauf_())
    assert gesehen == ["Bearer"]


# ---------- Pfade der Optionen, Freigabe, Bestätigung ----------
FREMDE_PFADE = [("--betreuer-pfad", "https://fremd.test/x"), ("--betreuer-pfad", "//fremd.test/x"), ("--betreuer-pfad", "GET //fremd.test/x"),
                ("--betreuer-pfad", "betreuer/status"), ("--betreuer-pfad", "/a//b"), ("--betreuer-pfad", "GET http://fremd.test/x"),
                ("--betreuer-pfad", "/x?a=https://fremd.test"), ("--platz-pfad", "https://fremd.test/{platz}"), ("--platz-pfad", "//fremd.test/{platz}"),
                ("--platz-pfad", "platz/{platz}")]


@pytest.mark.parametrize(("option", "wert"), FREMDE_PFADE)
def test_pfade_der_optionen_gehen_nie_an_einen_fremden_host(option, wert, capsys):
    def verboten(_anfrage):
        raise AssertionError("der Bearer darf nirgends hingehen")

    code = ab.main(["https://dienst.test", "--ja", option, wert], env={"WAMO_LABOR_TOKEN": TOKEN}, transport=httpx.MockTransport(verboten))
    ausgabe = capsys.readouterr()
    assert code == ab.EXIT_AUFRUF and "Pfad" in ausgabe.err and TOKEN not in ausgabe.out + ausgabe.err
    assert ab.main(["--attrappe", option, wert], env={}) == ab.EXIT_AUFRUF


def test_betreuer_pfad_nur_mit_get_ausser_mit_freigabe(capsys):
    assert ab.main(["--attrappe", "--betreuer-pfad", "POST /betreuer/reset"], env={}) == ab.EXIT_AUFRUF
    assert "--nicht-get-freigeben" in capsys.readouterr().err
    assert ab.main(["--attrappe", "--betreuer-pfad", "/betreuer/status", "--betreuer-pfad", "GET /betreuer/status", "--auswahl", "A9"], env={}) == ab.EXIT_TEILLAUF
    assert ab.main(["--attrappe", "--betreuer-pfad", "POST /betreuer/status", "--nicht-get-freigeben", "--auswahl", "A9"], env={}) == ab.EXIT_TEILLAUF


def test_a9_sendet_ohne_freigabe_nie_eine_andere_methode_als_get():
    methoden: list[str] = []

    def seite(scope):
        methoden.append(scope["method"])
        return 403, [("content-type", "application/json")], b"{}"

    with vw.starten() as ziel:
        transport = _mit_seiten(ziel, {"/betreuer/reset": seite})
        optionen = basis.Optionen(platz_token="P" * 40, betreuer_pfade=("POST /betreuer/reset",))
        e = ab.pruefe_ziel(ziel, auswahl=["A9"], optionen=optionen, transport=transport).nach_nr["A9"]
        assert methoden == [] and any("Freigabe" in rest for rest in e.von_hand)
        optionen = basis.Optionen(platz_token="P" * 40, betreuer_pfade=("POST /betreuer/reset",), nicht_get_frei=True)
        ab.pruefe_ziel(ziel, auswahl=["A9"], optionen=optionen, transport=transport)
    assert methoden == ["POST", "POST"]


def test_ohne_ja_wird_nichts_gesendet(capsys):
    def verboten(_anfrage):
        raise AssertionError("ohne --ja geht keine Anfrage hinaus")

    code = ab.main(["https://dienst.test:8443"], env={"WAMO_LABOR_TOKEN": TOKEN}, transport=httpx.MockTransport(verboten))
    ausgabe = capsys.readouterr()
    assert code == ab.EXIT_AUFRUF and "--ja" in ausgabe.err
    assert "dienst.test:8443" in ausgabe.err and "Platz 15" in ausgabe.err and "Platz 16" in ausgabe.err and TOKEN not in ausgabe.out + ausgabe.err


def test_attrappe_braucht_kein_ja():
    assert ab.main(["--attrappe", "--auswahl", "A9"], env={}) == ab.EXIT_TEILLAUF


# ---------- A4: strenger als der Vertrag ist weder grün noch rot ----------
@pytest.mark.parametrize(("name", "wert"), [("_QUOTE_RATE", 10), ("_QUOTE_TICKETS", 50), ("_QUOTE_KOMMENTARE", 200), ("_QUOTE_BYTES", 600 * 1024)])
def test_strengerer_dienst_ist_weder_gruen_noch_rot(name, wert):
    with mock.patch.object(labor_attrappe, name, wert):
        ergebnis = lauf(auswahl=["A4"])
    a4 = ergebnis.nach_nr["A4"]
    assert a4.status == basis.STRENGER, a4.meldung
    assert "strenger" in a4.meldung and any("strenger" in rest for rest in a4.von_hand)
    assert ab.exit_code(ergebnis) == ab.EXIT_TEILLAUF
    assert "Ergebnis: BESTANDEN" not in ab.tabelle(ergebnis, "Attrappe")


def test_dienst_mit_zehn_schreibaufrufen_je_minute_besteht_nie_still():
    with mock.patch.object(labor_attrappe, "_QUOTE_RATE", 10):
        ergebnis = lauf(auswahl=["A4"])
    assert "10" in ergebnis.nach_nr["A4"].meldung and ergebnis.nach_nr["A4"].status != basis.BESTANDEN


# ---------- Ausgang: abgebrochen, Skriptfehler, rot vor Abbruch ----------
def test_abbruch_ist_abgebrochen_und_nicht_durchgefallen(capsys):
    zaehler = {"n": 0}

    def stirbt(_anfrage):
        zaehler["n"] += 1
        if zaehler["n"] <= 4:
            return httpx.Response(200, json={"durchlauf_id": "5b0f0c1e-7a1d-4c61-9b52-0f6d2a8e41aa", "offen": True, "einwilligung_version": 1,
                                             "einwilligung_zeit": None, "tickets": [], "gesamt": 0})
        raise httpx.ConnectError("weg")

    code = ab.main(["https://dienst.test", "--ja", "--auswahl", "A1"], env={"WAMO_LABOR_TOKEN": TOKEN}, transport=httpx.MockTransport(stirbt))
    ausgabe = capsys.readouterr().out
    assert code == ab.EXIT_AUFRUF and "abgebrochen" in ausgabe and "0 durchgefallen" in ausgabe
    assert not re.search(r"^A1\s.*durchgefallen", ausgabe, re.M)


def test_rot_hat_vorrang_vor_abbruch_und_skriptfehler():
    rot = basis.Ergebnis("A2", "SQL-Injektion", basis.DURCHGEFALLEN, "Befund")
    kaputt = basis.Ergebnis("A3", "Prozent", basis.SKRIPTFEHLER, "Skriptfehler (ValueError)")
    assert ab.exit_code(ab.LaufErgebnis([rot], abbruch="Dienst weg")) == ab.EXIT_DURCHGEFALLEN
    assert ab.exit_code(ab.LaufErgebnis([rot, kaputt])) == ab.EXIT_DURCHGEFALLEN
    assert ab.exit_code(ab.LaufErgebnis([kaputt])) == ab.EXIT_AUFRUF


def test_skriptfehler_ist_kein_befund_ueber_den_dienst(monkeypatch, capsys):
    def kaputt(_z, _o):
        raise ValueError("Fehler im Skript")

    monkeypatch.setitem(pr.PRUEFUNGEN, "A1", ("XSS", kaputt))
    with vw.starten() as ziel:
        code = ab.main(["https://dienst.test", "--ja", "--auswahl", "A1"], env={"WAMO_LABOR_TOKEN": ziel.token}, transport=ziel.transport, takt=ziel.takt)
    ausgabe = capsys.readouterr().out
    assert code == ab.EXIT_AUFRUF and "Skriptfehler" in ausgabe and "0 durchgefallen" in ausgabe and "DURCHGEFALLEN" not in ausgabe


# ---------- A8 und A1: keine Zufallsgrüns ----------
def test_a8_nennt_stichprobe_und_den_beendeten_durchlauf_von_hand(sauber):
    a8 = sauber.nach_nr["A8"]
    assert "Stichprobe" in a8.meldung and "atomar" not in a8.meldung
    assert any("beendeten Durchlauf" in rest for rest in a8.von_hand)


# ---------- 60 KB ----------
def test_zehn_mittlere_tickets_liegen_zwischen_60_und_64_kb():
    """Zehn Tickets müssen über 60 KB und unter 64 KB wachsen, neun unter 60 KB: Ein Dienst mit der falschen Grenze 64 KB wird rot."""
    groessen: list[bytes] = []
    with vw.starten(defekte=("liste64",)) as ziel:
        async def app(scope, receive, send):
            async def abfangen(nachricht):
                if nachricht["type"] == "http.response.body" and scope.get("method") == "GET" and scope["path"] == "/api/kern/tickets" \
                        and not scope.get("query_string") and dict(scope["headers"]).get(b"x-wamo-platz") == b"16":
                    groessen.append(nachricht["body"])
                await send(nachricht)

            await ziel.app(scope, receive, abfangen)

        ergebnis = ab.pruefe_ziel(ziel, auswahl=["A11"], transport=httpx.ASGITransport(app=app))
    assert ergebnis.nach_nr["A11"].status == basis.DURCHGEFALLEN
    roh = max(groessen, key=len)
    assert 60 * 1024 < len(roh) <= 64 * 1024
    daten = json.loads(roh)
    daten["tickets"].pop()
    assert len(json.dumps(daten, ensure_ascii=False).encode()) < 60 * 1024


# ---------- Optionen und Verbindung ----------
def test_platz_token_steht_nicht_im_repr():
    optionen = basis.Optionen(platz_token="GEHEIMER-PLATZ-TOKEN-0123456789abcdef", board_url="https://dienst.test/board")
    assert "GEHEIMER" not in repr(optionen) and "dienst.test/board" in repr(optionen)


def test_ca_datei(tmp_path, capsys):
    kaputt = tmp_path / "ca.pem"
    kaputt.write_text("kein Zertifikat", encoding="utf-8")
    env = {"WAMO_LABOR_TOKEN": TOKEN}
    assert ab.main(["https://dienst.test", "--ja", "--ca-datei", str(kaputt)], env=env) == ab.EXIT_AUFRUF
    assert ab.main(["https://dienst.test", "--ja", "--ca-datei", str(tmp_path / "fehlt")], env=env) == ab.EXIT_AUFRUF
    assert ab.main(["https://dienst.test", "--ja"], env={**env, "WAMO_CA_FILE": str(kaputt)}) == ab.EXIT_AUFRUF
    assert capsys.readouterr().err.count("CA-Datei") == 3
    with vw.starten() as ziel:
        code = ab.main(["https://dienst.test", "--ja", "--auswahl", "A9", "--ca-datei", certifi.where()], env={"WAMO_LABOR_TOKEN": ziel.token},
                       transport=ziel.transport, takt=ziel.takt)
    assert code == ab.EXIT_TEILLAUF


def test_umleitung_wird_nie_befolgt(capsys):
    besucht: list[str] = []

    async def app(scope, receive, send):
        besucht.append(scope["path"])
        await send({"type": "http.response.start", "status": 307, "headers": [(b"location", b"https://fremd.test/anderswo")]})
        await send({"type": "http.response.body", "body": b""})

    code = ab.main(["https://dienst.test", "--ja"], env={"WAMO_LABOR_TOKEN": TOKEN}, transport=httpx.ASGITransport(app=app))
    ausgabe = capsys.readouterr()
    assert code == ab.EXIT_AUFRUF and "307" in ausgabe.err and besucht == ["/api/kern/durchlauf"]
