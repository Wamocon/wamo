"""IPP, PWG-Raster, eSCL, UPnP-SOAP und der kleine HTTP-Client (gegen einen lokalen Testserver, ohne Geräte)."""

import asyncio
import io
import re
import struct

import pytest
from PIL import Image

from wamo_geraete import raster
from wamo_geraete.http_klein import HttpFehler, _digest_kopf, anfrage
from wamo_geraete.treiber import Abgelehnt, GeraetFehler, escl, ipp, upnp


# ---------- IPP ----------
def test_ipp_kodieren_und_dekodieren():
    nutzlast = ipp.kodieren(ipp.OP_GET_PRINTER_ATTRIBUTES, 7, "ipp://192.168.178.24:631/ipp/print",
                            [(ipp.T_KEYWORD, "requested-attributes", ["printer-state", "marker-levels"])])
    assert nutzlast[:4] == b"\x02\x00\x00\x0b" and struct.unpack(">I", nutzlast[4:8])[0] == 7
    assert b"/ipp/print" in nutzlast and b"faxout" not in nutzlast
    # Antwort mit Integer, Enum, Bool, Text mit Sprache, Sammlung (wird übersprungen) und Mehrfachwerten
    antwort = struct.pack(">BBHI", 2, 0, 0, 7) + b"\x04"
    antwort += ipp._attr(ipp.T_ENUM, "printer-state", [3])
    antwort += ipp._attr(ipp.T_INTEGER, "marker-levels", [30, 10])
    antwort += ipp._attr(ipp.T_BOOLEAN, "printer-is-accepting-jobs", [True])
    lang = struct.pack(">H", 2) + b"de" + struct.pack(">H", 7) + b"Brother"
    antwort += struct.pack(">BH", 0x36, 22) + b"printer-make-and-model" + struct.pack(">H", len(lang)) + lang
    antwort += struct.pack(">BH", ipp.T_BEGCOL, 9) + b"media-col" + struct.pack(">H", 0)
    antwort += ipp._attr(ipp.T_KEYWORD, "x", ["innen"]) + struct.pack(">BHH", ipp.T_ENDCOL, 0, 0)
    antwort += b"\x03"
    status, attr = ipp.dekodieren(antwort)
    assert status == 0
    assert attr["printer-state"] == [3] and attr["marker-levels"] == [30, 10]
    assert attr["printer-is-accepting-jobs"] == [True] and attr["printer-make-and-model"] == ["Brother"]
    assert "x" not in attr  # Inhalt der Sammlung bleibt draußen
    s = ipp.status_aus(attr)
    assert s["zustand"] == "bereit" and s["nimmt_auftraege"] is True and s["modell"] == "Brother"


def test_ipp_kaputte_antwort():
    with pytest.raises(GeraetFehler):
        ipp.dekodieren(b"\x02\x00")
    with pytest.raises(GeraetFehler):
        ipp.dekodieren(struct.pack(">BBHI", 2, 0, 0, 1) + b"\x04\x21\x00\x05abc")


# ---------- Rasterung ----------
def test_pwg_zeile_rund():
    for zeile in (bytes([255] * 50), bytes([0, 1, 2, 3] * 40), bytes([7] * 300 + [1, 2] + [255] * 129),
                  bytes(range(256)), bytes([5])):
        kodiert = raster._zeile_kodieren(zeile, 1)
        # der Dekoder wirft bei einem Steuerbyte 128 (als Pixelwert darf 128 vorkommen)
        assert raster.pwg_zeilen_dekodieren(b"\x00" + kodiert, len(zeile), 1, 1) == zeile


def test_pwg_seite_rund_und_kopf():
    bild = Image.new("L", (120, 40), 255)
    for x in range(10, 60):
        bild.putpixel((x, 5), 0)
    daten = raster.pwg_raster([bild], 600, False)
    assert daten[:4] == b"RaS2" and daten[4:13] == b"PwgRaster"
    kopf = daten[4:4 + 1796]
    assert struct.unpack_from(">II", kopf, 372) == (120, 40)
    assert struct.unpack_from(">I", kopf, 400)[0] == 18  # sGray
    assert kopf[1732:1748] == b"iso_a4_210x297mm"
    assert raster.pwg_zeilen_dekodieren(daten[4 + 1796:], 120, 40, 1) == bild.tobytes()
    farbig = Image.new("RGB", (30, 4), (255, 255, 255))
    farbig.putpixel((3, 1), (244, 14, 14))
    daten = raster.pwg_raster([farbig], 600, True)
    assert raster.pwg_zeilen_dekodieren(daten[4 + 1796:], 30, 4, 3) == farbig.tobytes()


def test_dokument_seiten_und_grenze():
    dok = raster.dokument_aus({"art": "dokument", "titel": "Angebot",
                               "text": "# Überschrift\n- Punkt eins\n\n" + "Ein langer Absatz mit Text. " * 40})
    seiten = raster.seiten(dok, dpi=100)
    assert len(seiten) == 1 and seiten[0].size == (827, 1169)
    tabelle = raster.dokument_aus({"art": "tabelle", "titel": "T", "spalten": ["A", "B"], "zeilen": [["1", "2"]] * 5})
    assert len(raster.seiten(tabelle, dpi=100, farbe=True)) == 1
    lang = raster.dokument_aus({"art": "code", "titel": "Lang", "text": "zeile\n" * 3000})
    with pytest.raises(Abgelehnt):
        raster.seiten(lang, dpi=100)
    for falsch in ({"art": "bpmn", "titel": "x", "text": "y"}, {"art": "dokument", "titel": "x"}, "text",
                   {"art": "tabelle", "titel": "x", "spalten": [], "zeilen": []}):
        with pytest.raises(Abgelehnt):
            raster.dokument_aus(falsch)


# ---------- eSCL ----------
def test_escl_location_nur_eigener_ort():
    assert escl.job_pfad("http://192.168.178.24/eSCL/ScanJobs/abc-123", "192.168.178.24") == "/eSCL/ScanJobs/abc-123"
    assert escl.job_pfad("/eSCL/ScanJobs/abc", "192.168.178.24") == "/eSCL/ScanJobs/abc"
    for falsch in ("http://192.168.178.99/eSCL/ScanJobs/x", "http://BRW405BD8EBD7BE.local/eSCL/ScanJobs/x",
                   "/eSCL/../admin", "/eSCL/ScanJobs/x/../../y", "https://192.168.178.24/eSCL/ScanJobs/x",
                   "/eSCL/ScanJobs/x?y=1", "/net/net/airprint.html"):
        with pytest.raises(GeraetFehler):
            escl.job_pfad(falsch, "192.168.178.24")


def test_escl_einstellungen_fest():
    xml = escl.einstellungen("einzug", True, 300).decode()
    assert "<pwg:InputSource>Feeder</pwg:InputSource>" in xml and "RGB24" in xml and "image/jpeg" in xml
    for args in (("drucker", False, 300), ("glas", False, 1200), ("glas", "ja", 300)):
        with pytest.raises(Abgelehnt):
            escl.einstellungen(*args)


def test_pdf_aus_jpeg_seiten():
    puffer = io.BytesIO()
    Image.new("L", (100, 140), 200).save(puffer, "JPEG")
    pdf = escl.pdf_aus_seiten([puffer.getvalue(), puffer.getvalue()], 150)
    assert pdf.startswith(b"%PDF") and pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ") >= 1
    puffer = io.BytesIO()
    Image.new("L", (10, 10), 0).save(puffer, "PNG")
    with pytest.raises(GeraetFehler):
        escl.pdf_aus_seiten([puffer.getvalue()], 150)


# ---------- UPnP ----------
VERBOTEN = ("AddPortMapping", "DeletePortMapping", "ForceTermination", "SetConnectionType", "RequestConnection",
            "SetAVTransportURI", "X_AVM-DE_SetHostEntry", "Reboot", "FactoryReset", "SetEnable")


def test_upnp_nur_feste_aktionen():
    namen = {a[2] for a in upnp.AKTIONEN.values()}
    assert not namen & set(VERBOTEN)
    assert all(re.fullmatch(r"(Get\w+|SetVolume|SetMute|X_AVM-DE_GetHostListPath)", n) for n in namen)
    for schluessel in upnp.AKTIONEN:
        werte = {a: (0 if a != "Channel" else "Master") for a in upnp.AKTIONEN[schluessel][3]}
        soapaction, rumpf = upnp.soap_rumpf(schluessel, werte)
        assert upnp.AKTIONEN[schluessel][2] in soapaction
    with pytest.raises(KeyError):
        upnp.soap_rumpf("AddPortMapping", {})
    with pytest.raises(ValueError):
        upnp.soap_rumpf("rc_lautstaerke_setzen", {"InstanceID": 0, "Channel": "Master", "DesiredVolume": "<x/>"})
    with pytest.raises(ValueError):
        upnp.soap_rumpf("router_link", {"NewX": 1})


def test_upnp_antwort_und_fehler():
    werte = upnp.soap_werte(b"<s:Envelope><s:Body><u:GetVolumeResponse><CurrentVolume>23</CurrentVolume>"
                            b"</u:GetVolumeResponse></s:Body></s:Envelope>")
    assert werte["CurrentVolume"] == "23"
    with pytest.raises(GeraetFehler):
        upnp.soap_werte(b"<s:Envelope><s:Body><s:Fault><detail><errorCode>401</errorCode></detail></s:Fault>")


# ---------- HTTP-Client ----------
async def _testserver(antworten: list[bytes], gesehen: list[bytes]):
    async def behandeln(reader, writer):
        daten = await reader.readuntil(b"\r\n\r\n")
        laenge = re.search(rb"Content-Length: (\d+)", daten)
        if laenge:
            daten += await reader.readexactly(int(laenge.group(1)))
        gesehen.append(daten)
        writer.write(antworten.pop(0))
        await writer.drain()
        writer.close()
    return await asyncio.start_server(behandeln, "127.0.0.1", 0)


async def test_http_client_grenzen_und_chunked():
    gesehen: list[bytes] = []
    server = await _testserver([
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhallo",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: 99999\r\n\r\n",
        b"HTTP/1.1 302 Found\r\nLocation: http://8.8.8.8/\r\nContent-Length: 0\r\n\r\n",
    ], gesehen)
    port = server.sockets[0].getsockname()[1]
    async with server:
        a = await anfrage("127.0.0.1", port, "GET", "/x")
        assert a.status == 200 and a.rumpf == b"hallo"
        assert b"Host: 127.0.0.1:" in gesehen[0] and b"Connection: close" in gesehen[0]
        a = await anfrage("127.0.0.1", port, "POST", "/y", rumpf=b"{}")
        assert a.rumpf == b"abcde"
        with pytest.raises(HttpFehler):
            await anfrage("127.0.0.1", port, "GET", "/gross", max_bytes=100)
        a = await anfrage("127.0.0.1", port, "GET", "/weiter")
        assert a.status == 302 and len(gesehen) == 4  # keine Weiterleitung gefolgt
    with pytest.raises(Exception):
        await anfrage("fritz.box", 80, "GET", "/")  # Namen sind nicht erlaubt
    with pytest.raises(HttpFehler):
        await anfrage("127.0.0.1", 1, "GET", "/x\r\nHost: boese")
    with pytest.raises(HttpFehler):
        await anfrage("127.0.0.1", 1, "GET", "/", kopf={"X": "a\r\nb"})


async def test_http_digest_wiederholung():
    gesehen: list[bytes] = []
    server = await _testserver([
        b'HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Digest realm="shelly", nonce="abc", qop="auth", '
        b'algorithm=SHA-256\r\nContent-Length: 0\r\n\r\n',
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}",
    ], gesehen)
    port = server.sockets[0].getsockname()[1]
    async with server:
        a = await anfrage("127.0.0.1", port, "GET", "/rpc/Switch.GetStatus?id=0", digest=("admin", "geheim"))
    assert a.status == 200
    assert b"Authorization: Digest" in gesehen[1] and b"geheim" not in gesehen[1]


def test_digest_kopf_md5_und_unbekannt():
    kopf = _digest_kopf('Digest realm="r", nonce="n"', "GET", "/", "u", "p")
    assert kopf.startswith("Digest ") and "algorithm=MD5" in kopf
    assert _digest_kopf('Basic realm="r"', "GET", "/", "u", "p") is None
    assert _digest_kopf('Digest realm="r", nonce="n", algorithm=SHA-512', "GET", "/", "u", "p") is None
