"""Tests für scripts/wamo_labor_abnahme.py (Abnahme des Test-Jira-Dienstes gegen den Vertrag, docs/labor-dienst-vertrag.md Abschnitt 7).

Aufruf (aus scripts, mit der Umgebung von voice-core):
  D:/IDEA/Projekt/wamo/voice-core/.venv/Scripts/python.exe -m pytest -p no:cacheprovider -q test_wamo_labor_abnahme.py
Kein Netz und kein Server: Die Attrappe läuft im Prozess (httpx.ASGITransport), die Uhr ist eine falsche Uhr, die Wartezeiten der
Schreibrate kosten nichts. Das Skript gilt erst als Prüfmittel, wenn es gegen die saubere Nachbildung besteht (grün) und gegen jede
absichtlich verwundbare Variante durchfällt (rot), und zwar an der Anforderung, die der Fehler bricht.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor_abnahme as ab  # noqa: E402
import wamo_labor_abnahme_basis as basis  # noqa: E402
import wamo_labor_abnahme_pruefungen as pr  # noqa: E402
import wamo_labor_abnahme_verwundbar as vw  # noqa: E402

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
        code = ab.main(["https://dienst.test", "--auswahl", "A1"], env={"WAMO_LABOR_TOKEN": ziel.token}, transport=ziel.transport, takt=ziel.takt)
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
    datei = tmp_path / "token.txt"
    datei.write_text(TOKEN + "\n", encoding="utf-8")
    with vw.starten() as ziel:
        ziel_token = ziel.token
        datei.write_text(ziel_token + "\n", encoding="utf-8")
        code = ab.main(["https://dienst.test", "--token-datei", str(datei), "--auswahl", "A9"], env={}, transport=ziel.transport, takt=ziel.takt)
    assert code == ab.EXIT_TEILLAUF
    assert ziel_token not in capsys.readouterr().out


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

    code = ab.main(["https://dienst.test:8443"], env={"WAMO_LABOR_TOKEN": TOKEN}, transport=httpx.MockTransport(abgelehnt))
    ausgabe = capsys.readouterr()
    assert code == ab.EXIT_AUFRUF and "nicht erreichbar" in ausgabe.err and "https://dienst.test:8443" in ausgabe.err
    assert TOKEN not in ausgabe.out + ausgabe.err


def test_geschlossener_port_auf_loopback(capsys):
    assert ab.main(["http://127.0.0.1:1", "--frist", "4"], env={"WAMO_LABOR_TOKEN": TOKEN}) == ab.EXIT_AUFRUF
    assert "nicht erreichbar" in capsys.readouterr().err


def test_zeitueberschreitung_endet_mit_klarer_meldung(capsys):
    async def langsam(scope, receive, send):
        await asyncio.sleep(30)

    code = ab.main(["https://dienst.test", "--frist", "0.2"], env={"WAMO_LABOR_TOKEN": TOKEN}, transport=httpx.ASGITransport(app=langsam))
    ausgabe = capsys.readouterr()
    assert code == ab.EXIT_AUFRUF and "Zeitüberschreitung" in ausgabe.err and "0.2" in ausgabe.err
    assert TOKEN not in ausgabe.out + ausgabe.err


def test_dienst_antwortet_nicht_wie_im_vertrag(capsys):
    async def html(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/html")]})
        await send({"type": "http.response.body", "body": b"<html>Willkommen</html>"})

    code = ab.main(["https://dienst.test"], env={"WAMO_LABOR_TOKEN": TOKEN}, transport=httpx.ASGITransport(app=html))
    assert code == ab.EXIT_AUFRUF and "V1" in capsys.readouterr().err


def test_ausfall_mitten_im_lauf_bricht_ab_und_zeigt_die_tabelle(capsys):
    zaehler = {"n": 0}

    def stirbt(anfrage):
        zaehler["n"] += 1
        if zaehler["n"] <= 4:  # V1 und V2 je Platz laufen noch
            return httpx.Response(200, json={"durchlauf_id": "5b0f0c1e-7a1d-4c61-9b52-0f6d2a8e41aa", "offen": True, "einwilligung_version": 1,
                                             "einwilligung_zeit": None, "tickets": [], "gesamt": 0})
        raise httpx.ConnectError("weg")

    code = ab.main(["https://dienst.test", "--auswahl", "A1"], env={"WAMO_LABOR_TOKEN": TOKEN}, transport=httpx.MockTransport(stirbt))
    ausgabe = capsys.readouterr()
    assert code == ab.EXIT_AUFRUF and "antwortet nicht mehr" in ausgabe.err


# ---------- Ausgangsstand der Plätze ----------
def test_platz_ohne_offenen_durchlauf_wird_nicht_geprueft(capsys):
    with vw.starten() as ziel:
        ziel.attrappe.durchlauf_beenden(15)
        code = ab.main(["https://dienst.test"], env={"WAMO_LABOR_TOKEN": ziel.token}, transport=ziel.transport, takt=ziel.takt)
    ausgabe = capsys.readouterr()
    assert code == ab.EXIT_AUFRUF and "Platz 15" in ausgabe.err and "Durchlauf" in ausgabe.err


def test_benutzter_platz_verlangt_einen_neuen_durchlauf(capsys):
    with vw.starten() as ziel:
        erst = ab.main(["https://dienst.test", "--auswahl", "A1"], env={"WAMO_LABOR_TOKEN": ziel.token}, transport=ziel.transport, takt=ziel.takt)
        zweit = ab.main(["https://dienst.test", "--auswahl", "A1"], env={"WAMO_LABOR_TOKEN": ziel.token}, transport=ziel.transport, takt=ziel.takt)
        ziel.attrappe.neuer_durchlauf(15)
        ziel.attrappe.neuer_durchlauf(16)
        wieder = ab.main(["https://dienst.test", "--auswahl", "A1"], env={"WAMO_LABOR_TOKEN": ziel.token}, transport=ziel.transport, takt=ziel.takt)
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
    gesehen: set[str] = set()
    with vw.starten() as ziel:
        async def app(scope, receive, send):
            if scope["type"] == "http":
                gesehen.add(dict(scope["headers"]).get(b"x-wamo-platz", b"(keiner)").decode())
            await ziel.app(scope, receive, send)

        ab.pruefe_ziel(ziel, transport=httpx.ASGITransport(app=app))
    assert {"15", "16"} <= gesehen
    assert not gesehen & {str(n) for n in range(1, 15)}, sorted(gesehen)  # nie ein Besucherplatz


def test_trocken_gilt_nur_mit_adresse(capsys):
    assert ab.main(["--attrappe", "--trocken"], env={}) == ab.EXIT_AUFRUF
    assert "--trocken" in capsys.readouterr().err


def test_tabelle_nennt_je_anforderung_das_ergebnis(sauber):
    text = ab.tabelle(sauber, "Attrappe")
    for nr in ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "A11"):
        assert any(zeile.startswith(nr + " ") for zeile in text.splitlines()), nr
    assert "bestanden" in text and "von Hand" in text
    assert "BESTANDEN" in text.splitlines()[-1] or "BESTANDEN" in text


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
    roh = _a1_mit_board(GUTE_KOEPFE, b"<html><script>alert(1)</script></html>").nach_nr["A1"]
    assert roh.status == basis.DURCHGEFALLEN and "unmaskiert" in roh.meldung


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
