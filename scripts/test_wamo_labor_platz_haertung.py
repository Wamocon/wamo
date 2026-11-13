"""Tests zu den Befunden der Sicherheitsprüfung des Platz-Skripts: CA festnageln, Sicherung und Platzdatei nie ungeprüft verwenden,
Reichweite (Schalter --kiosk-konto, Administratorwarnung, fremde Werte), Weiterleitung, certutil mit vollem Pfad, verlustfreie Sicherung.

Wie in den anderen Tests wird nichts Echtes berührt (Attrappen und Fallen). Der Fingerabdruck der CA wird nie als Literal in einen Test
geschrieben: Er wird aus der Datei im Repo berechnet und mit der Konstante im Skript verglichen.
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import http.client
import json
import re
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wamo_labor_platz_attrappe as at  # noqa: E402
import wamo_labor_platz_testwelt as tw  # noqa: E402
from wamo_labor_platz_umgebung import Abruf, AbrufFehler  # noqa: E402

REPO_CA = Path(__file__).resolve().parent.parent / "infra" / "sokrates2" / "wamo-ca.crt"
CHROME_PFAD = "Software\\Policies\\Google\\Chrome"
BOARD = "https://192.168.178.136:55447/board"
FREMDE_DER = tw.DER[:-1] + bytes([tw.DER[-1] ^ 1])


@pytest.fixture(autouse=True)
def _fallen(monkeypatch, tmp_path):
    tw.fallen_stellen(monkeypatch, tmp_path / "echt")


@pytest.fixture
def welt(tmp_path):
    return tw.Welt(tmp_path / "w")


@pytest.fixture
def eingerichtet(welt):
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 0
    welt.zeilen.clear()
    return welt


def _fremde_ca(welt) -> Path:
    pfad = welt.tmp / "fremd.crt"
    pfad.write_text(tw.pem_aus(FREMDE_DER), encoding="ascii", newline="\n")
    return pfad


def _unveraendert(welt, vorher: dict) -> bool:
    return (welt.reg.momentaufnahme() == vorher["reg"] and welt.zert.aenderungen == vorher["zert"] and welt.dateien() == vorher["dateien"])


def _stand(welt) -> dict:
    return {"reg": welt.reg.momentaufnahme(), "zert": welt.zert.aenderungen, "dateien": welt.dateien()}


# ---------- 1. CA festnageln ----------

def test_konstanten_halten_die_ca_im_repo_fest():
    import wamo_labor_platz_umgebung as um
    import wamo_labor_platz_zertifikat as zt
    roh = REPO_CA.read_text(encoding="ascii")
    der = base64.b64decode("".join(re.search(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", roh, re.S).group(1).split()))
    hinweis = "Die CA im Repo hat sich geändert: CA_SHA256 und CA_SHA1 in wamo_labor_platz_zertifikat.py neu aus der Datei berechnen"
    assert zt.CA_SHA256 == hashlib.sha256(der).hexdigest(), hinweis
    assert zt.CA_SHA1 == hashlib.sha1(der).hexdigest(), hinweis
    standard = {f.name: f.default for f in dataclasses.fields(um.Umgebung)}
    assert standard["ca_sha256"] == zt.CA_SHA256 and standard["ca_sha1"] == zt.CA_SHA1


@pytest.mark.parametrize("weg", ["option", "kandidat", "kandidat_vor_gueltiger"])
def test_fremde_ca_datei_wird_vor_jeder_aenderung_abgelehnt(welt, weg):
    fremd = _fremde_ca(welt)
    vorher = _stand(welt)
    if weg == "option":
        argv = ["--ca-datei", str(fremd)]
    else:
        welt.umg.ca_kandidaten = (fremd,) if weg == "kandidat" else (fremd, welt.ca)  # die erste vorhandene muss stimmen
        argv = []
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "3", *argv) == 2
    assert "weicht" in welt.text and "WAMO-CA" in welt.text
    assert welt.zert.aufrufe == [] and _unveraendert(welt, vorher)


def test_import_laeuft_aus_einer_geprueften_kopie_im_eigenen_ordner(welt):
    kopie = welt.umg.platz_ordner / "wamo-ca.crt"
    gesehen = {}

    def quelle_vertauschen():
        gesehen["kopie"] = kopie.read_text(encoding="ascii")
        welt.ca.write_text(tw.pem_aus(FREMDE_DER), encoding="ascii")  # die Quelle ändert sich nach der Prüfung

    welt.zert.vor_aenderung = quelle_vertauschen
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 0
    assert gesehen["kopie"] == tw.pem_aus(tw.DER)
    assert welt.zert.speicher == {tw.SHA1}
    assert not kopie.exists()


def test_quelldatei_wird_nur_einmal_gelesen(welt, monkeypatch):
    gelesen = []
    echt_bytes, echt_text = Path.read_bytes, Path.read_text
    monkeypatch.setattr(Path, "read_bytes", lambda self: gelesen.append(self) or echt_bytes(self))
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: gelesen.append(self) or echt_text(self, *a, **k))
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 0
    assert gelesen.count(welt.ca) == 1


def test_importieren_prueft_die_kopie_vor_dem_certutil_erneut(tmp_path):
    import wamo_labor_platz_zertifikat as zt
    kopie = tmp_path / "kopie.crt"
    kopie.write_text(tw.pem_aus(FREMDE_DER), encoding="ascii")
    zert = at.FakeZertifikatsspeicher()
    with pytest.raises(zt.CaFehler):
        zt.importieren(zert, kopie, tw.SHA1, tw.SHA256)
    assert zert.aufrufe == [] and zert.speicher == set()


def test_pruefen_und_tls_nehmen_nur_die_feste_ca(eingerichtet, tmp_path):
    import wamo_labor_platz_betrieb as be
    eingerichtet.umg.abruf = at.AbrufAttrappe(Abruf(200, None))
    assert eingerichtet.lauf("pruefen", "--ca-datei", str(_fremde_ca(eingerichtet))) == 1
    assert "weicht" in eingerichtet.text and eingerichtet.umg.abruf.aufrufe == []
    with pytest.raises(AbrufFehler) as e:
        be.tls_kontext(_fremde_ca(eingerichtet))
    assert e.value.art == "tls"
    assert len(be.tls_kontext(REPO_CA).get_ca_certs()) == 1


# ---------- 2. Sicherung und Platzdatei nie ungeprüft ----------

def _json_aendern(pfad: Path, aendern) -> None:
    daten = json.loads(pfad.read_text(encoding="utf-8"))
    aendern(daten)
    pfad.write_text(json.dumps(daten), encoding="utf-8")


SICHERUNG_KAPUTT = {
    "ca_form": lambda d: d["ca"].update(sha1="XYZ"),
    "ca_gross": lambda d: d["ca"].update(sha1=tw.SHA1.upper()),
    "ca_kurz": lambda d: d["ca"].update(sha1=tw.SHA1[:39]),
    "ca_fremd": lambda d: d["ca"].update(sha1=tw.SHA1[::-1]),
    "ca_fehlt": lambda d: d.pop("ca"),
    "schluessel_fehlt": lambda d: d.pop("schluessel"),
    "richtlinien_liste": lambda d: d.update(richtlinien=[]),
    "eintrag_text": lambda d: d["richtlinien"].update(TranslateEnabled="x"),
    "typ_unbekannt": lambda d: d["richtlinien"]["TranslateEnabled"].update(typ="binary"),
    "wert_falsch": lambda d: d["richtlinien"]["TranslateEnabled"].update(da=True, typ="dword", wert="1"),
    "name_fremd": lambda d: d["richtlinien"].update(Boese={"da": False, "typ": "dword"}),
    "bool_text": lambda d: d.update(profil_vorher="ja"),
    "version": lambda d: d.update(version=2),
    "verknuepfungen": lambda d: d.update(verknuepfungen=[1]),
    "platz_json_zahl": lambda d: d.update(platz_json_vorher=5),
}


@pytest.mark.parametrize("name", sorted(SICHERUNG_KAPUTT))
def test_beschaedigte_sicherung_bricht_ab_ohne_aenderung(eingerichtet, name):
    _json_aendern(eingerichtet.sicherung, SICHERUNG_KAPUTT[name])
    vorher = _stand(eingerichtet)
    assert eingerichtet.lauf("rueckweg") == 1
    assert "Sicherung" in eingerichtet.text
    assert _unveraendert(eingerichtet, vorher) and not any(a[1] == "-delstore" for a in eingerichtet.zert.aufrufe)
    eingerichtet.zeilen.clear()
    assert eingerichtet.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 1 and "Sicherung" in eingerichtet.text
    assert _unveraendert(eingerichtet, vorher)


@pytest.mark.parametrize("inhalt", ["[]", "{kaputt", '"text"', ""])
def test_sicherung_mit_falscher_form_ist_ein_fehler_kein_absturz(eingerichtet, inhalt):
    eingerichtet.sicherung.write_text(inhalt, encoding="utf-8")
    assert eingerichtet.lauf("rueckweg") == 1 and "Sicherung" in eingerichtet.text and eingerichtet.sicherung.exists()


PLATZ_KAPUTT = {
    "ca_sha1_fehlt": lambda d: d.pop("ca_sha1"),
    "ca_sha1_form": lambda d: d.update(ca_sha1="nicht-hex"),
    "ca_sha1_fremd": lambda d: d.update(ca_sha1=tw.SHA1[::-1]),
    "platz_text": lambda d: d.update(platz="3"),
    "platz_bool": lambda d: d.update(platz=True),
    "platz_null": lambda d: d.update(platz=0),
    "platz_gross": lambda d: d.update(platz=17),
    "wamo_pfad": lambda d: d.update(wamo_url="https://192.168.178.136:55445/x"),
    "board_pfad": lambda d: d.update(board_url="https://192.168.178.136:55447/anderer"),
    "board_schraegstrich": lambda d: d.update(board_url=BOARD + "/"),
    "board_fehlt": lambda d: d.pop("board_url"),
    "start_fehlt": lambda d: d.pop("start"),
    "start_text": lambda d: d.update(start="python"),
    "start_python": lambda d: d["start"].update(python=5),
    "profil_fehlt": lambda d: d.pop("profil"),
    "version_fehlt": lambda d: d.pop("version"),
}


@pytest.mark.parametrize("name", sorted(PLATZ_KAPUTT))
def test_beschaedigte_platzdatei_ist_ein_fehler_nie_ein_absturz(eingerichtet, name):
    _json_aendern(eingerichtet.platz_datei, PLATZ_KAPUTT[name])
    eingerichtet.umg.abruf = at.AbrufAttrappe(Abruf(200, None))

    def verboten():
        raise AssertionError("Token wurde erfragt")
    eingerichtet.umg.token_eingabe = verboten
    assert eingerichtet.lauf("status") == 1 and "platz.json" in eingerichtet.text
    assert eingerichtet.lauf("pruefen") == 1 and any(z.startswith("[FEHLER] Platz") for z in eingerichtet.zeilen)
    assert eingerichtet.lauf("starten") == 1 and eingerichtet.prozesse.aufrufe == []
    assert eingerichtet.lauf("einrichten", "--kiosk-konto") == 1  # ohne --platz wird die Nummer aus der Datei gebraucht
    assert not any("boese" in u for u, _ in eingerichtet.umg.abruf.aufrufe)


def test_platzdatei_mit_anderer_adresse_auf_den_festen_ports_wird_abgelehnt(eingerichtet):
    _json_aendern(eingerichtet.platz_datei, lambda d: d.update(board_url="https://192.168.178.136:55447/anderer"))
    assert eingerichtet.lauf("starten") == 1 and "verändert" in eingerichtet.text


# ---------- 3. Reichweite ----------

def test_einrichten_verlangt_den_schalter_kiosk_konto(welt):
    vorher = _stand(welt)
    assert welt.lauf("einrichten", "--platz", "3") == 2
    assert "--kiosk-konto" in welt.text and "Kiosk-Konto" in welt.text and _unveraendert(welt, vorher)
    assert welt.lauf("einrichten", "--trocken", "--platz", "3") == 0  # der Plan braucht den Schalter nicht
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 0


def test_administratorrechte_geben_eine_deutliche_warnung(welt):
    welt.lauf("einrichten", "--trocken", "--platz", "3")
    assert "Administratorrechte" not in welt.text
    welt.zeilen.clear()
    welt.admin = True
    assert welt.lauf("einrichten", "--trocken", "--platz", "3") == 0
    assert "Administratorrechte" in welt.text and "kein Kiosk-Konto" in welt.text
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 0  # Warnung, aber kein Abbruch: der Schalter bleibt die Bestätigung


def test_fremde_werte_im_richtlinienschluessel_werden_gemeldet(welt):
    welt.lauf("einrichten", "--trocken", "--platz", "3")
    assert "fremde Werte" not in welt.text
    welt.zeilen.clear()
    welt.reg.vorbelegen("HKCU", CHROME_PFAD, {"HardwareAccelerationModeEnabled": ("dword", 0), "TranslateEnabled": ("dword", 1)})
    welt.reg.vorbelegen("HKCU", CHROME_PFAD + "\\ExtensionSettings", {})
    vorher = _stand(welt)
    assert welt.lauf("einrichten", "--trocken", "--platz", "3") == 0
    zeile = next(z for z in welt.zeilen if "fremde Werte" in z)
    assert "HardwareAccelerationModeEnabled" in zeile and "extensionsettings" in zeile.lower() and "TranslateEnabled" not in zeile
    assert _unveraendert(welt, vorher)


# ---------- 6. Weiterleitung ----------

def _abruf(port: int, pfad: str):
    verbindung = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    verbindung.request("GET", pfad)
    antwort = verbindung.getresponse()
    ergebnis = (antwort.status, antwort.getheader("Location"))
    verbindung.close()
    return ergebnis


def test_pfad_wird_zeitkonstant_und_ohne_absturz_verglichen(monkeypatch):
    import wamo_labor_platz_start as st
    aufrufe = []
    echt = st.hmac.compare_digest
    monkeypatch.setattr(st.hmac, "compare_digest", lambda a, b: aufrufe.append((a, b)) or echt(a, b))
    with st.EinmalWeiterleitung(0, BOARD, 3.0) as w:
        with socket.create_connection(("127.0.0.1", w.port), timeout=3) as s:
            s.sendall(b"GET /weiter/\xe4 HTTP/1.0\r\nHost: 127.0.0.1:%d\r\n\r\n" % w.port)
            assert s.recv(64).split(b"\r\n")[0].endswith(b"404 Not Found")  # Nicht-ASCII-Zeichen führen nicht zum Absturz
        assert _abruf(w.port, urlsplit(w.adresse).path)[0] == 302
    assert aufrufe and all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in aufrufe)


def test_misslungene_auslieferung_verbraucht_den_einmalwert_nicht(monkeypatch):
    import wamo_labor_platz_start as st
    original, zaehler = st._Handler._antwort, {"n": 0}

    def antwort(self, code, ort=None):
        zaehler["n"] += 1
        if zaehler["n"] == 1 and code == 302:
            raise BrokenPipeError("Gegenstelle weg")
        return original(self, code, ort)

    monkeypatch.setattr(st._Handler, "_antwort", antwort)
    with st.EinmalWeiterleitung(0, BOARD + "#t=" + tw.TOKEN, 3.0) as w:
        pfad = urlsplit(w.adresse).path
        with pytest.raises((http.client.HTTPException, ConnectionError)):
            _abruf(w.port, pfad)
        assert not w.abgeholt
        assert _abruf(w.port, pfad) == (302, BOARD + "#t=" + tw.TOKEN) and w.abwarten()
        assert _abruf(w.port, pfad)[0] == 404  # erfolgreich ausgeliefert heißt: genau einmal


# ---------- 7. certutil mit vollem Pfad ----------

def test_certutil_wird_mit_vollem_pfad_aus_system32_aufgerufen(monkeypatch, tmp_path):
    import wamo_labor_platz_zertifikat as zt
    monkeypatch.setattr(zt, "system32_ordner", lambda: tmp_path / "win" / "System32")
    gesehen = {}

    class Ergebnis:
        returncode, stdout, stderr = 0, "ok", ""

    monkeypatch.setattr(subprocess, "run", lambda args, **kw: gesehen.update(args=args, kw=kw) or Ergebnis())
    assert zt.certutil_echt(["-user", "-store", "Root", tw.SHA1]) == (0, "ok")
    assert gesehen["args"] == [str(tmp_path / "win" / "System32" / "certutil.exe"), "-user", "-store", "Root", tw.SHA1]
    assert not gesehen["kw"].get("shell")


@pytest.mark.skipif(sys.platform != "win32", reason="Systemordner nur unter Windows")
def test_system32_ordner_ist_der_echte_systemordner():
    import wamo_labor_platz_zertifikat as zt
    ordner = zt.system32_ordner()
    assert ordner.is_dir() and ordner.name.lower() == "system32" and (ordner / "certutil.exe").is_file()


def test_administratorpruefung_der_echten_umgebung_ist_ein_wahrheitswert():
    import wamo_labor_platz_betrieb as be
    assert isinstance(be.ist_admin_echt(), bool)


# ---------- 9. Registrierung verlustfrei sichern ----------

NICHT_SICHERBAR = {
    "binaerwert": lambda reg: reg.vorbelegen("HKCU", CHROME_PFAD, {"TranslateEnabled": ("andere", b"\x01")}),
    "listenwert_dword": lambda reg: reg.vorbelegen("HKCU", CHROME_PFAD + "\\URLBlocklist", {"1": ("dword", 5)}),
    "listenname_text": lambda reg: reg.vorbelegen("HKCU", CHROME_PFAD + "\\URLBlocklist", {"eins": ("sz", "x")}),
    "liste_mit_unterschluessel": lambda reg: reg.vorbelegen("HKCU", CHROME_PFAD + "\\URLBlocklist\\tief", {}),
}


@pytest.mark.parametrize("name", sorted(NICHT_SICHERBAR))
@pytest.mark.parametrize("trocken", [False, True])
def test_nicht_verlustfrei_sicherbarer_wert_bricht_vor_jeder_aenderung_ab(welt, name, trocken):
    NICHT_SICHERBAR[name](welt.reg)
    vorher = _stand(welt)
    argv = ["einrichten", "--platz", "3"] + (["--trocken"] if trocken else ["--kiosk-konto"])
    assert welt.lauf(*argv) == 1
    assert "nicht verlustfrei" in welt.text
    assert _unveraendert(welt, vorher) and welt.reg.schreibzugriffe == 0 and not welt.sicherung.exists()


def test_anderer_aber_sicherbarer_typ_wird_mit_dem_richtigen_typ_zurueckgeschrieben(welt):
    welt.reg.vorbelegen("HKCU", CHROME_PFAD, {"TranslateEnabled": ("sz", "1")})
    vorher = welt.reg.momentaufnahme()
    assert welt.lauf("einrichten", "--kiosk-konto", "--platz", "3") == 0
    assert welt.reg.werte("HKCU", CHROME_PFAD)["TranslateEnabled"] == ("dword", 0)
    assert welt.lauf("rueckweg") == 0
    assert welt.reg.momentaufnahme() == vorher
