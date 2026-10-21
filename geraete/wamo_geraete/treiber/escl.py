"""Scanner über eSCL (Brother MFC-L3750CDW, http://192.168.178.24/eSCL/, Version 2.63, Glas und Einzug).

Status: ScannerStatus (Zustand, Einzug). Scannen: ScanJobs mit festen Einstellungen (A4, Glas oder Einzug,
Graustufen oder Farbe, 150 oder 300 dpi, JPEG je Seite), danach NextDocument bis 404. Die Location muss auf demselben
Gerät unter /eSCL/ScanJobs/ liegen, sonst wird nichts nachgeladen. Aus den Seiten baut Pillow ein PDF; die Bilder
werden dabei dekodiert (Grenze 40 Megapixel je Seite, 50 Seiten). Der Inhalt geht nie an das Sprachmodell.
"""

from __future__ import annotations

import asyncio
import io
import re
import time
from urllib.parse import urlsplit

from PIL import Image

from ..http_klein import HttpFehler, anfrage
from ..konfig import Geraet
from . import Abgelehnt, GeraetFehler, NichtEingerichtet

Image.MAX_IMAGE_PIXELS = 40_000_000
MAX_SEITEN = 50
MAX_SEITE_BYTES = 25 * 1024 * 1024
SCAN_ZEIT_SECS = 300.0
JOB_RE = re.compile(r"^/eSCL/ScanJobs/[A-Za-z0-9-]{1,64}$")
QUELLEN = {"glas": "Platen", "einzug": "Feeder"}
FARBEN = {False: "Grayscale8", True: "RGB24"}
AUFLOESUNGEN = (150, 300)


def _wert(xml: str, tag: str) -> str:
    m = re.search(rf"<(?:\w+:)?{tag}>([^<]{{0,80}})</(?:\w+:)?{tag}>", xml)
    return m.group(1).strip() if m else ""


async def status(geraet: Geraet) -> dict:
    if not geraet.adresse:
        raise NichtEingerichtet("Adresse des Scanners fehlt")
    try:
        antwort = await anfrage(geraet.adresse, geraet.port or 80, "GET", "/eSCL/ScannerStatus", timeout=4,
                                max_bytes=64 * 1024)
    except HttpFehler as e:
        raise GeraetFehler(f"Scanner nicht erreichbar ({e})") from e
    if antwort.status != 200:
        raise GeraetFehler(f"Scanner antwortet mit HTTP {antwort.status}")
    xml = antwort.rumpf.decode("utf-8", "replace")
    zustand = {"Idle": "bereit", "Processing": "scannt", "Testing": "prüft", "Stopped": "angehalten",
               "Down": "gestört"}.get(_wert(xml, "State"), "unbekannt")
    einzug = {"ScannerAdfLoaded": "Papier eingelegt", "ScannerAdfEmpty": "leer", "ScannerAdfJam": "Papierstau",
              "ScannerAdfMispick": "Einzugsfehler", "ScannerAdfHatchOpen": "Klappe offen"}.get(
                  _wert(xml, "AdfState"), "unbekannt")
    return {"erreichbar": True, "zustand": zustand, "einzug": einzug}


def einstellungen(quelle: str, farbe: bool, aufloesung: int) -> bytes:
    if quelle not in QUELLEN or aufloesung not in AUFLOESUNGEN or not isinstance(farbe, bool):
        raise Abgelehnt("Scan-Einstellungen ungültig")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" '
        'xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">'
        "<pwg:Version>2.63</pwg:Version><scan:Intent>Document</scan:Intent>"
        "<pwg:ScanRegions><pwg:ScanRegion><pwg:Height>3507</pwg:Height>"
        "<pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>"
        "<pwg:Width>2480</pwg:Width><pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset>"
        "</pwg:ScanRegion></pwg:ScanRegions>"
        f"<pwg:InputSource>{QUELLEN[quelle]}</pwg:InputSource>"
        f"<scan:ColorMode>{FARBEN[farbe]}</scan:ColorMode>"
        f"<scan:XResolution>{aufloesung}</scan:XResolution><scan:YResolution>{aufloesung}</scan:YResolution>"
        "<pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>"
        "<scan:DocumentFormatExt>image/jpeg</scan:DocumentFormatExt>"
        "</scan:ScanSettings>"
    ).encode("utf-8")


def job_pfad(location: str, adresse: str) -> str:
    """Pfad des Scan-Auftrags aus dem Location-Kopf: relativ oder absolut auf genau diesem Gerät (IP), nur unter
    /eSCL/ScanJobs/. Alles andere wird abgelehnt (der Scanner darf uns nirgendwohin schicken)."""
    teile = urlsplit(location.strip())
    if teile.scheme or teile.netloc:
        if teile.scheme != "http" or teile.hostname != adresse or (teile.port not in (None, 80)):
            raise GeraetFehler("Scanner nennt einen fremden Ort für den Auftrag")
    pfad = teile.path.rstrip("/")
    if not JOB_RE.fullmatch(pfad) or teile.query or teile.fragment:
        raise GeraetFehler("Scanner nennt einen unerwarteten Ort für den Auftrag")
    return pfad


def pdf_aus_seiten(seiten: list[bytes], aufloesung: int) -> bytes:
    bilder = []
    for daten in seiten:
        with Image.open(io.BytesIO(daten)) as roh:
            if roh.format != "JPEG":
                raise GeraetFehler("Scanner liefert kein JPEG")
            roh.load()
            bilder.append(roh.convert("RGB" if roh.mode not in ("L", "RGB") else roh.mode))
    if not bilder:
        raise GeraetFehler("Keine Seite gescannt")
    puffer = io.BytesIO()
    bilder[0].save(puffer, "PDF", save_all=True, append_images=bilder[1:], resolution=float(aufloesung))
    return puffer.getvalue()


async def scannen(geraet: Geraet, quelle: str, farbe: bool, aufloesung: int) -> tuple[bytes, int]:
    """Scannt und liefert (PDF, Seitenzahl)."""
    if not geraet.adresse:
        raise NichtEingerichtet("Adresse des Scanners fehlt")
    port = geraet.port or 80
    try:
        antwort = await anfrage(geraet.adresse, port, "POST", "/eSCL/ScanJobs", kopf={"Content-Type": "text/xml"},
                                rumpf=einstellungen(quelle, farbe, aufloesung), timeout=15, max_bytes=16 * 1024)
    except HttpFehler as e:
        raise GeraetFehler(f"Scanner nicht erreichbar ({e})") from e
    if antwort.status == 503:
        raise GeraetFehler("Scanner ist gerade beschäftigt")
    if antwort.status not in (200, 201) or "location" not in antwort.kopf:
        raise GeraetFehler(f"Scanner nimmt den Auftrag nicht an (HTTP {antwort.status})")
    pfad = job_pfad(antwort.kopf["location"], geraet.adresse)
    seiten: list[bytes] = []
    ende = time.monotonic() + SCAN_ZEIT_SECS
    try:
        while len(seiten) < MAX_SEITEN:
            if time.monotonic() > ende:
                raise GeraetFehler("Scan dauert zu lange")
            try:
                seite = await anfrage(geraet.adresse, port, "GET", f"{pfad}/NextDocument", timeout=90,
                                      max_bytes=MAX_SEITE_BYTES)
            except HttpFehler as e:
                raise GeraetFehler(f"Scan abgebrochen ({e})") from e
            if seite.status == 404:
                break
            if seite.status == 503:
                await asyncio.sleep(1.0)
                continue
            if seite.status != 200:
                raise GeraetFehler(f"Scanner meldet HTTP {seite.status}")
            seiten.append(seite.rumpf)
            if quelle == "glas" and len(seiten) > 1:
                raise GeraetFehler("Scanner liefert vom Glas mehr als eine Seite")
    except BaseException:
        try:
            await anfrage(geraet.adresse, port, "DELETE", pfad, timeout=5, max_bytes=4096)
        except HttpFehler:
            pass
        raise
    pdf = await asyncio.to_thread(pdf_aus_seiten, seiten, aufloesung)
    return pdf, len(seiten)
