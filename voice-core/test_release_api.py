"""Tests des Weges vom Kern zum Release-Dienst (release_client.py) und der Route GET /wamo/api/release/status (release_api.py), Schritt 1.5.

Der Release-Dienst wird durch einen echten Unix-Socket mit Attrappe ersetzt (nur Linux). Geprüft werden die Zeile, die der Kern schickt, die streng
gefilterte Sicht (nichts anderes als bekannte Felder erreicht die Oberfläche), die Rolle admin, die Rate-Grenze und der Ausfall des Dienstes.

Ausführen:  uv run python -m pytest -q test_release_api.py   (im Ordner voice-core/)
"""

import json
import os
import re
import socket
import sys
import tempfile
import threading

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import release_api as api
import release_client as C
import wamo_api
from auth_gate import Identity

LINUX = hasattr(socket, "AF_UNIX") and sys.platform.startswith("linux")
ADMIN = "123e4567-e89b-12d3-a456-426614174000"
KENNUNG = "0b9f2c1e-4a6d-4f0e-9a1b-2c3d4e5f6a7b"

STATUS = {
    "ok": True, "text": "Release-Dienst läuft", "version": "0.1", "zeit": "2026-11-02T13:00:00Z", "modus": "geruest", "bereit": True, "defekt": None,
    "anfrage": {"id": KENNUNG, "status": "pruefung1", "quelle": "main", "profil": ADMIN, "seq": 3, "schritt": 2, "schritte": 11, "seit": "2026-11-02T12:00:00Z",
                "frist_bis": "2026-11-02T14:00:00Z", "versuch": 1, "fertig": False, "version": "0.1.1", "klasse": "normal"},
    "abgeschlossen": [{"id": KENNUNG, "status": "abgebrochen", "grund": "zeit"}], "angehalten": None,
    "anker": {"erreichbar": True, "sperre": "an"}, "datenbank": "angeschlossen", "arbeiter": "frei",
    "audit": {"kette": "ok", "zeilen": 70, "letzter_hash": "a" * 64, "grund": None, "fuellgrad_prozent": 0.1},
    "spiegel": {"rueckstand": 0, "defekt": None}, "abweichung": None, "rueckweg_offen": None, "speicherfehler": None,
    "installation": "ok", "installation_probleme": None,
}


class Dienst:
    """Ein Unix-Socket, der wie ctl.sock antwortet und die empfangenen Zeilen aufhebt."""

    def __init__(self, antwort: bytes | None = None):
        self.dir = tempfile.mkdtemp(prefix="release-api-")
        self.pfad = os.path.join(self.dir, "ctl.sock")
        self.zeilen: list[bytes] = []
        self.antwort = json.dumps(STATUS).encode() + b"\n" if antwort is None else antwort
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.pfad)
        self.srv.listen(8)
        self.t = threading.Thread(target=self._lauf, daemon=True)
        self.t.start()

    def _lauf(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            with conn:
                daten = b""
                while not daten.endswith(b"\n"):
                    block = conn.recv(4096)
                    if not block:
                        break
                    daten += block
                self.zeilen.append(daten)
                conn.sendall(self.antwort)

    def schliessen(self):
        self.srv.close()


@pytest.fixture
def dienst(monkeypatch):
    if not LINUX:
        pytest.skip("Unix-Sockets nur unter Linux")
    d = Dienst()
    monkeypatch.setenv("WAMO_RELEASE_SOCKET", d.pfad)
    yield d
    d.schliessen()


# ------------------------------------------------------------------------------------------------ Client
def test_anfrage_schickt_genau_eine_zeile_mit_frischer_kennung(dienst):
    C.anfrage("status")
    C.anfrage("status")
    a, b = dienst.zeilen
    for z in (a, b):
        assert re.fullmatch(rb"[0-9a-f]{32} status\n", z)
    assert a != b   # der Dienst lehnt eine wiederholte Kennung ab


def test_sicht_enthaelt_nur_bekannte_felder(dienst):
    s = C.status()
    assert set(s) == {"verfuegbar", "modus", "version", "bereit", "defekt", "angehalten", "anker", "datenbank", "arbeiter", "audit_kette", "installation",
                      "anfrage", "abgeschlossen", "zeit"}
    assert s["anker"] == {"erreichbar": True, "sperre": "an"}
    assert (s["modus"], s["datenbank"], s["arbeiter"], s["audit_kette"], s["installation"]) == ("geruest", "angeschlossen", "frei", "ok", "ok")
    assert s["anfrage"]["id"] == KENNUNG and s["anfrage"]["schritt"] == 2 and s["anfrage"]["version"] == "0.1.1"
    assert s["anfrage"]["profil"] == ADMIN   # der Client reicht es nur an die Route weiter; sie macht daraus von_mir und entfernt es (unten)
    text = json.dumps(s)
    assert "a" * 64 not in text   # kein Hash der Audit-Kette


def test_sicht_faengt_falsche_formen_ab():
    kaputt = {"ok": True, "modus": "Geruest!", "bereit": "ja", "defekt": "x" * 500, "anker": "text", "audit": [], "datenbank": "irgendwas",
              "arbeiter": 5, "installation": "abweichung", "anfrage": {"id": "../../etc/passwd"}, "abgeschlossen": [{"id": "x"}, 5, {"id": KENNUNG, "status": "A B"}],
              "version": ["0.1"], "zeit": 12}
    s = C.ansicht(kaputt)
    assert s["modus"] == "unbekannt" and s["bereit"] is False and len(s["defekt"]) == 80
    assert s["anker"] == {"erreichbar": False, "sperre": "unbekannt"}
    assert (s["datenbank"], s["arbeiter"], s["audit_kette"], s["installation"]) == ("unbekannt", "unbekannt", "defekt", "abweichung")
    assert s["anfrage"] is None
    assert s["abgeschlossen"] == [{"id": KENNUNG, "status": None, "grund": None}]
    assert s["version"] == "unbekannt" or s["version"] is None
    assert s["zeit"] is None


def test_texte_ohne_steuerzeichen_und_gekuerzt():
    s = C.ansicht({"ok": True, "defekt": "Fehler\x00\x1b[31m rot\nzweite Zeile", "angehalten": "a" * 300})
    assert "\x00" not in s["defekt"] and "\x1b" not in s["defekt"] and "\n" not in s["defekt"]
    assert len(s["angehalten"]) == 80


def test_abgelehnte_antwort_wird_zum_kurzen_grund():
    if not LINUX:
        pytest.skip("Unix-Sockets nur unter Linux")
    for roh, grund in ((b'{"ok": false, "grund": "gesperrt"}\n', "gesperrt"), (b'{"ok": false, "grund": "B\xc3\xb6ser Text mit Pfad /etc"}\n', "abgelehnt"),
                       (b'{"ok": false}\n', "abgelehnt"), (b"kein json\n", "antwort_ungueltig"), (b"[1]\n", "antwort_ungueltig"),
                       (b'{"ok": "ja"}\n', "antwort_ungueltig"), (b"", "keine_antwort")):
        d = Dienst(roh)
        try:
            with pytest.raises(C.ReleaseFehler) as e:
                C.anfrage("status", d.pfad)
            assert e.value.grund == grund, roh
        finally:
            d.schliessen()


def test_dienst_fehlt_oder_zu_gross(tmp_path):
    with pytest.raises(C.ReleaseFehler) as e:
        C.anfrage("status", str(tmp_path / "gibt-es-nicht.sock"))
    assert e.value.grund == "nicht_verfuegbar"
    if LINUX:
        d = Dienst(b'{"ok": true, "x": "' + b"a" * (C.ANTWORT_MAX + 10) + b'"}\n')
        try:
            with pytest.raises(C.ReleaseFehler) as e:
                C.anfrage("status", d.pfad)
            assert e.value.grund in ("keine_antwort", "antwort_ungueltig")
        finally:
            d.schliessen()


def test_befehl_wird_vor_dem_senden_geprueft():
    for befehl in ("status; rm", "Status", "a", "status\n", "x" * 30, ""):
        with pytest.raises(ValueError):
            C.anfrage(befehl, "/gibt/es/nicht")


def test_client_kennt_nur_lesen():
    quelle = open(C.__file__, encoding="utf-8").read()
    code = re.sub(r'""".*?"""', "", quelle, flags=re.S)
    assert "freigeben" not in code.lower()
    assert "anfordern" not in code   # vorschlagen kommt erst mit Schritt 3.4


# ------------------------------------------------------------------------------------------------ Route
class Kontext:
    rolle = "admin"
    gesperrt = False


@pytest.fixture
def ctx(monkeypatch):
    k = Kontext()

    async def identitaet(_request):
        if k.gesperrt:
            raise HTTPException(403, "kein aktives WAMO-Profil oder zweiter Faktor fehlt")
        return Identity(user_id=ADMIN, name="Admin", rolle=k.rolle, anrede="du", anmeldung=1.0)

    monkeypatch.setattr(api, "identitaet", identitaet)
    api._versuche.clear()
    return k


@pytest.fixture
def client(ctx):
    app = FastAPI()
    app.include_router(api.router, prefix="/wamo/api")
    return TestClient(app)


def test_admin_bekommt_die_sicht(client, dienst):
    r = client.get("/wamo/api/release/status")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    j = r.json()
    assert j["verfuegbar"] is True and j["anker"]["sperre"] == "an" and j["anfrage"]["status"] == "pruefung1"


def test_mitglied_wird_abgewiesen_ohne_den_dienst_zu_fragen(client, ctx, dienst):
    ctx.rolle = "mitglied"
    assert client.get("/wamo/api/release/status").status_code == 403
    assert dienst.zeilen == []


def test_ohne_anmeldung_403(client, ctx, dienst):
    ctx.gesperrt = True
    assert client.get("/wamo/api/release/status").status_code == 403
    assert dienst.zeilen == []


def test_dienst_nicht_erreichbar_ist_eine_normale_antwort(client, monkeypatch, tmp_path):
    monkeypatch.setenv("WAMO_RELEASE_SOCKET", str(tmp_path / "weg.sock"))
    r = client.get("/wamo/api/release/status")
    assert r.status_code == 200
    assert r.json() == {"verfuegbar": False, "grund": "nicht_verfuegbar"}


def test_rate_grenze(client, dienst):
    for _ in range(api.STATUS_GRENZE[0]):
        assert client.get("/wamo/api/release/status").status_code == 200
    assert client.get("/wamo/api/release/status").status_code == 429


def test_nur_get_und_kein_schreibender_weg(client, dienst):
    for methode in ("post", "put", "delete", "patch"):
        assert getattr(client, methode)("/wamo/api/release/status").status_code == 405
    assert [r.path for r in api.router.routes] == ["/release/status"]


def test_route_ist_im_kern_registriert():
    assert "release_api" in wamo_api.ROUTEN_MODULE


# ------------------------------------------------------------------------------------------------ Commit, Tree, Prüfstand und der Block pruefung (Schritt 1.5)
V_COMMIT, TREE = "d" * 40, "e" * 40
ANDERES_PROFIL = "223e4567-e89b-12d3-a456-426614174000"


def neue_anfrage(**zusatz):
    a = dict(STATUS["anfrage"], status="wartet_freigabe", v_commit=V_COMMIT, tree_id=TREE, treffer_anzahl=2, klasse="sonder",
             pruefstand={"bauen": "ok", "tests": "ok", "secret_scan": "ok", "fremdverweis": "ok", "version_bytegleich": "ok", "schutzschicht": "ok"})
    a.update(zusatz)
    return a


def block():
    return {"diff": {"anzahl": 2, "hinzu": 7, "weg": 1, "binaer": 1, "gekappt": False,
                     "dateien": [{"s": "M", "p": "voice-core/bot.py", "a": 6, "d": 1}, {"s": "A", "p": "ui/bild.png", "a": None, "d": None}]},
            "wirkung": {"anzahl": 2, "nach_art": {"netzziel": 1, "geheimnisname": 1}, "sonder_hinweis": True, "neue_pakete": 1, "windows": False,
                        "treffer": [{"p": "host/a.py", "z": 3, "a": "netzziel", "d": "example.org"}, {"p": "host/b.py", "z": 9, "a": "geheimnisname", "d": "core.env"}]},
            "empfehlung": {"stufe": "minor", "version": "0.2.0", "feat": 1, "fix": 0, "sonst": 2, "unkonventionell": 0, "major_null": False}}


@pytest.fixture
def mit_dienst_antwort(monkeypatch):
    """Ersetzt den Socket durch eine feste Antwort (läuft auch unter Windows)."""
    def setze(anfrage_dict):
        monkeypatch.setattr(C, "anfrage", lambda *_a, **_k: dict(STATUS, anfrage=anfrage_dict))
    return setze


def test_sicht_prueft_commit_und_tree():
    s = C.ansicht(dict(STATUS, anfrage=neue_anfrage()))["anfrage"]
    assert (s["v_commit"], s["tree_id"], s["treffer_anzahl"]) == (V_COMMIT, TREE, 2)
    assert s["pruefstand"]["schutzschicht"] == "ok" and len(s["pruefstand"]) == 6
    assert C._commit("a" * 40) == "a" * 40
    for schlecht in ("A" * 40, "a" * 39, "a" * 41, "a" * 64, "g" * 40, "a" * 39 + "\n", " " + "a" * 39, "../" + "a" * 37, 5, None, ["a" * 40], {"a": 1}, True):
        assert C._commit(schlecht) is None, schlecht


def test_falsche_hex_werte_fallen_weg(client, mit_dienst_antwort):
    mit_dienst_antwort(neue_anfrage(v_commit="Z" * 40, tree_id="e" * 64))
    a = client.get("/wamo/api/release/status").json()["anfrage"]
    assert (a["v_commit"], a["tree_id"]) == (None, None)
    assert a["status"] == "wartet_freigabe"   # der Rest der Anfrage bleibt
    for k in ("v_commit", "tree_id"):
        for schlecht in ("1" * 40 + "x", "", "abc", 40, "a" * 40 + "\x00"):
            mit_dienst_antwort(neue_anfrage(**{k: schlecht}))
            assert client.get("/wamo/api/release/status").json()["anfrage"][k] is None


def test_sicht_kappt_listen_und_texte():
    p = block()
    p["diff"]["dateien"] = [{"s": "M", "p": f"ordner/ä‮{i}/" + "x" * 300, "a": i, "d": 0} for i in range(100)]
    p["wirkung"]["treffer"] = [{"p": "p/" + "y" * 300, "z": i, "a": "netzziel", "d": "\x1b[31m" + "d" * 300 + "\n"} for i in range(100)]
    p["wirkung"]["nach_art"] = {"netzziel": 3, "boese art": 1, "x" * 50: 2, "prozess": "viele", "sudo": 10**9}
    s = C.ansicht(dict(STATUS, anfrage=neue_anfrage(pruefstand={f"zeile_{i:02d}": "ok" for i in range(40)}, pruefung=p)), mit_pruefung=True)["anfrage"]
    assert len(s["pruefstand"]) == 16
    d, w = s["pruefung"]["diff"], s["pruefung"]["wirkung"]
    assert len(d["dateien"]) == C.DATEIEN_MAX == 20 and len(w["treffer"]) == C.TREFFER_MAX == 15
    for e in d["dateien"]:
        assert len(e["p"]) <= 70 and e["p"].isascii() and "?" in e["p"] and "‮" not in e["p"]
    for e in w["treffer"]:
        assert len(e["p"]) <= 70 and len(e["d"]) <= 40 and e["d"].isascii() and "\x1b" not in e["d"] and "\n" not in e["d"]
    assert w["nach_art"] == {"netzziel": 3}   # nur Wörter der Liste mit gültiger Zahl


def test_sicht_laesst_nur_bekannte_formen_durch():
    p = block()
    p["diff"]["FREMD1"] = "x"
    p["diff"]["dateien"][0]["FREMD2"] = "y"
    p["wirkung"]["treffer"][0]["FREMD3"] = "z"
    p["empfehlung"]["FREMD4"] = "rm"
    p["FREMD5"] = {"a": 1}
    s = C.ansicht(dict(STATUS, anfrage=neue_anfrage(pruefung=p, zusatz="x")), mit_pruefung=True)["anfrage"]
    assert set(s["pruefung"]) == {"diff", "wirkung", "empfehlung"}
    assert set(s["pruefung"]["diff"]) == {"anzahl", "hinzu", "weg", "binaer", "gekappt", "dateien"}
    assert set(s["pruefung"]["diff"]["dateien"][0]) == {"s", "p", "a", "d"}
    assert set(s["pruefung"]["wirkung"]) == {"anzahl", "nach_art", "sonder_hinweis", "neue_pakete", "windows", "treffer"}
    assert set(s["pruefung"]["wirkung"]["treffer"][0]) == {"p", "z", "a", "d"}
    assert set(s["pruefung"]["empfehlung"]) == {"stufe", "version", "feat", "fix", "sonst", "unkonventionell", "major_null"}
    assert "zusatz" not in s
    text = json.dumps(s)
    for fremd in ("FREMD1", "FREMD2", "FREMD3", "FREMD4", "FREMD5"):
        assert fremd not in text
    assert s["pruefung"]["diff"]["dateien"][1] == {"s": "A", "p": "ui/bild.png", "a": None, "d": None}   # Binärdateien bleiben null


def test_falsche_formen_der_pruefung_fallen_auf_none():
    def sicht(p):
        return C.ansicht(dict(STATUS, anfrage=neue_anfrage(pruefung=p)), mit_pruefung=True)["anfrage"]["pruefung"]
    for kaputt in ("text", ["a"], 5, True, {}, {"diff": 1, "wirkung": "x", "empfehlung": []}):
        assert sicht(kaputt) is None, kaputt
    teile = sicht({"diff": "kaputt", "wirkung": block()["wirkung"], "empfehlung": dict(block()["empfehlung"], stufe="gigantisch")})
    assert teile["diff"] is None and teile["empfehlung"] is None and teile["wirkung"]["anzahl"] == 2   # jeder Teil für sich
    for aenderung in ({"gekappt": "ja"}, {"anzahl": -1}, {"hinzu": True}, {"weg": 10**12}, {"dateien": "x"}, {"binaer": None}):
        p = block()
        p["diff"].update(aenderung)
        assert sicht(p)["diff"] is None, aenderung
    p = block()
    p["diff"]["dateien"] = [{"s": "Z", "p": "a", "a": 1, "d": 1}, {"s": "M", "p": 5, "a": 1, "d": 1}, {"s": "M", "p": "b", "a": "1", "d": 1}, {"s": "M", "p": "c", "a": 10**9, "d": 1},
                            {"s": "M", "p": "ok", "a": 1, "d": 2}, 7, None]
    assert sicht(p)["diff"]["dateien"] == [{"s": "M", "p": "ok", "a": 1, "d": 2}]   # ungültige Einträge fallen einzeln weg
    for stufe in ("keine", "patch", "minor", "major"):
        assert sicht(dict(block(), empfehlung=dict(block()["empfehlung"], stufe=stufe)))["empfehlung"]["stufe"] == stufe
    for aenderung in ({"version": "1.2"}, {"version": "v1.2.3"}, {"version": 5}, {"major_null": "nein"}, {"feat": -1}, {"fix": 10**9}, {"stufe": "Major"}, {"stufe": None}):
        assert sicht(dict(block(), empfehlung=dict(block()["empfehlung"], **aenderung)))["empfehlung"] is None, aenderung
    assert sicht(dict(block(), empfehlung=dict(block()["empfehlung"], version="")))["empfehlung"]["version"] == ""   # unbekannte Basis: leere Nummer
    p = block()
    p["wirkung"]["treffer"] = [{"p": "a", "z": 1, "a": "unbekannte_art", "d": ""}, {"p": "a", "z": -1, "a": "netzziel", "d": ""}, {"p": "a", "z": 1, "a": "netzziel", "d": "ok"}]
    assert sicht(p)["wirkung"]["treffer"] == [{"p": "a", "z": 1, "a": "netzziel", "d": "ok"}]
    for aenderung in ({"sonder_hinweis": 1}, {"windows": None}, {"neue_pakete": 10**6}, {"nach_art": []}, {"treffer": {}}, {"anzahl": "2"}):
        p = block()
        p["wirkung"].update(aenderung)
        assert sicht(p)["wirkung"] is None, aenderung


def test_pruefung_nur_mit_ausdruecklicher_freigabe_der_route():
    antwort = dict(STATUS, anfrage=neue_anfrage(pruefung=block()))
    assert C.ansicht(antwort)["anfrage"]["pruefung"] is None   # das Sprachwerkzeug (ohne mit_pruefung) bekommt den Pfadblock nie
    assert C.ansicht(antwort, mit_pruefung=True)["anfrage"]["pruefung"]["diff"]["dateien"][0]["p"] == "voice-core/bot.py"
    import inspect
    assert inspect.signature(C.status).parameters["mit_pruefung"].default is False
    assert "mit_pruefung=True" in open(api.__file__, encoding="utf-8").read()


def test_von_mir_statt_profil(client, mit_dienst_antwort):
    mit_dienst_antwort(neue_anfrage(profil=ADMIN, pruefung=block()))
    r = client.get("/wamo/api/release/status")
    a = r.json()["anfrage"]
    assert a["von_mir"] is True and "profil" not in a
    assert ADMIN not in r.text   # die Kennung der Person steht nirgends in der Antwort
    mit_dienst_antwort(neue_anfrage(profil=ANDERES_PROFIL))
    r = client.get("/wamo/api/release/status")
    assert r.json()["anfrage"]["von_mir"] is False and ANDERES_PROFIL not in r.text and "profil" not in r.json()["anfrage"]
    for schlecht in (None, 5, "kein profil", "", ADMIN + "x", ADMIN.upper()):
        mit_dienst_antwort(neue_anfrage(profil=schlecht))
        a = client.get("/wamo/api/release/status").json()["anfrage"]
        assert a["von_mir"] is False and "profil" not in a, schlecht   # auch die Großschreibung gilt nicht: der Client lässt nur die Form des Dienstes durch


def test_route_liefert_den_vertrag(client, mit_dienst_antwort):
    mit_dienst_antwort(neue_anfrage(profil=ADMIN, pruefung=block()))
    a = client.get("/wamo/api/release/status").json()["anfrage"]
    assert set(a) == {"id", "status", "quelle", "schritt", "schritte", "seit", "frist_bis", "versuch", "fertig", "version", "klasse", "v_commit", "tree_id", "von_mir",
                      "treffer_anzahl", "pruefstand", "pruefung"}
    assert (a["v_commit"], a["tree_id"], a["von_mir"], a["treffer_anzahl"]) == (V_COMMIT, TREE, True, 2)
    assert a["pruefung"] == block()


def test_alter_dienst_ohne_pruefung_funktioniert(client, mit_dienst_antwort):
    mit_dienst_antwort(STATUS["anfrage"])   # die Antwort eines Dienstes vor dieser Änderung
    r = client.get("/wamo/api/release/status")
    assert r.status_code == 200
    a = r.json()["anfrage"]
    assert (a["v_commit"], a["tree_id"], a["von_mir"], a["treffer_anzahl"], a["pruefstand"], a["pruefung"]) == (None, None, True, 0, {}, None)
    assert a["status"] == "pruefung1" and a["schritt"] == 2
    assert r.json()["verfuegbar"] is True


def test_keine_anfrage_bleibt_null(client, mit_dienst_antwort):
    mit_dienst_antwort(None)
    j = client.get("/wamo/api/release/status").json()
    assert j["verfuegbar"] is True and j["anfrage"] is None
