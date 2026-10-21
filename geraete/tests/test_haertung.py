"""Härtungen aus dem Review: Seitenreservierung, gedrosseltes Audit, geschützte Steckdosen, PDF aus JPEG, Geräteliste."""

import io
import json
import uuid

import pytest
from PIL import Image

from wamo_geraete import dienst as d
from wamo_geraete.ablage import Ablage
from wamo_geraete.audit import Audit
from wamo_geraete.grenzen import Grenzen
from wamo_geraete.konfig import aus_daten
from wamo_geraete.treiber import GeraetFehler, escl, upnp

ANNA = str(uuid.uuid4())


def test_seiten_reservieren_und_zurueck():
    g = Grenzen()
    tag = g.seiten_reservieren(ANNA, 8, 10, 100)
    assert tag
    assert g.seiten_reservieren(ANNA, 3, 10, 100) is None  # 8 + 3 > 10
    g.seiten_zurueck(ANNA, 8, tag)
    assert g.seiten_reservieren(ANNA, 10, 10, 100)  # wieder frei
    g.seiten_zurueck(ANNA, 10, "anderer-tag")  # falscher Tag: nichts zurückgenommen
    assert g.seiten_reservieren(ANNA, 1, 10, 100) is None


def test_audit_drosselt_abgelehnte_und_kuerzt(tmp_path):
    t = [0.0]
    pfad = str(tmp_path / "audit.jsonl")
    audit = Audit(pfad, uhr=lambda: t[0])
    for i in range(50):
        audit.schreiben({"werkzeug": "x" * 200, "ergebnis": "rejected"}, drosseln=True)
    audit.schreiben({"werkzeug": "ping", "ergebnis": "success"})  # nicht gedrosselt
    t[0] = 61.0
    audit.schreiben({"werkzeug": "ping", "ergebnis": "success"})
    zeilen = [json.loads(z) for z in open(pfad, encoding="utf-8")]
    abgelehnt = [z for z in zeilen if z["ergebnis"] == "rejected"]
    assert len(abgelehnt) == 30 and all(len(z["werkzeug"]) == 64 for z in abgelehnt)
    sammel = [z for z in zeilen if z["ergebnis"] == "weitere abgelehnte Anfragen"]
    assert len(sammel) == 1 and sammel[0]["anzahl"] == 20


@pytest.fixture
def dienst(tmp_path):
    konfig = aus_daten({"geraet": [
        {"kennung": "heizung", "klasse": "steckdose", "treiber": "shelly", "adresse": "192.168.178.126", "schutz": True},
    ]})
    return d.Dienst(konfig, ablage=Ablage(str(tmp_path / "a")), audit=Audit(None), grenzen=Grenzen())


def _anfrage(**argumente):
    return {"v": 1, "werkzeug": "steuern", "geraet": "heizung", "argumente": argumente,
            "kontext": {"user_id": ANNA, "rolle": "mitglied", "session_id": str(uuid.uuid4()), "name": "Anna"}}


async def test_geschuetzte_steckdose_nur_mit_freigabe(dienst, monkeypatch):
    aufrufe = []

    async def schalten(g, k, aktion):
        aufrufe.append(aktion)
        return {"an": aktion == "an"}
    monkeypatch.setattr(d.shelly, "steuern", schalten)
    antwort, _ = await dienst.bearbeiten(_anfrage(aktion="an"))
    assert antwort["status"] == "rejected" and not aufrufe
    antwort, _ = await dienst.bearbeiten(_anfrage(aktion="an", freigegeben="ja"))  # nur ein echtes True zählt
    assert antwort["status"] == "rejected" and not aufrufe
    antwort, _ = await dienst.bearbeiten(_anfrage(aktion="an", freigegeben=True))
    assert antwort["status"] == "success" and aufrufe == ["an"]
    liste, _ = await dienst.bearbeiten({**_anfrage(), "werkzeug": "geraete", "geraet": None})
    assert next(x for x in liste["geraete"] if x["kennung"] == "heizung")["schutz"] is True


def _jpeg(farbmodus="RGB", groesse=(40, 30)):
    puffer = io.BytesIO()
    Image.new(farbmodus, groesse, 128 if farbmodus == "L" else (200, 10, 10)).save(puffer, "JPEG")
    return puffer.getvalue()


def test_pdf_bettet_jpeg_unveraendert_ein():
    jpeg = _jpeg()
    pdf = escl.pdf_aus_seiten([jpeg, _jpeg("L")], 150)
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    assert jpeg in pdf and pdf.count(b"/DCTDecode") == 2 and b"/Count 2" in pdf
    # startxref zeigt auf die Tabelle
    stand = int(pdf.rsplit(b"startxref\n", 1)[1].split(b"\n", 1)[0])
    assert pdf[stand:stand + 4] == b"xref"


def test_pdf_lehnt_unpassende_seiten_ab():
    with pytest.raises(GeraetFehler):
        escl.pdf_aus_seiten([], 150)
    with pytest.raises(GeraetFehler):
        escl.pdf_aus_seiten([b"kein jpeg"], 150)
    png = io.BytesIO()
    Image.new("RGB", (10, 10)).save(png, "PNG")
    with pytest.raises(GeraetFehler):
        escl.pdf_aus_seiten([png.getvalue()], 150)
    with pytest.raises(GeraetFehler):
        escl.pdf_aus_seiten([_jpeg(groesse=(4000, 3500))], 150)  # 14 Megapixel


@pytest.mark.parametrize("ort", ["http://[::1/eSCL/ScanJobs/a", "http://192.168.178.24:99999/eSCL/ScanJobs/a",
                                 "http://192.168.178.99/eSCL/ScanJobs/a", "/eSCL/andere/a"])
def test_job_pfad_lehnt_kaputte_orte_ab(ort):
    with pytest.raises(GeraetFehler):
        escl.job_pfad(ort, "192.168.178.24")


def test_geraeteliste_linear_und_begrenzt():
    text = "<Item><HostName>a</HostName></Item><Item>offen" + "<Item>" * 5000
    assert upnp._items(text) == ["<HostName>a</HostName>"]
    assert len(upnp._items("<Item>x</Item>" * 1000)) == 300
