"""Scanner über eSCL (Brother MFC-L3750CDW, http://192.168.178.24/eSCL/, Version 2.63, Glas und Einzug).

Status: ScannerStatus (Zustand, Einzug). Scannen: ScanJobs mit festen Einstellungen (A4, Glas oder Einzug,
Graustufen oder Farbe, 150 oder 300 dpi, JPEG je Seite), danach NextDocument bis 404. Die Location muss auf demselben
Gerät unter /eSCL/ScanJobs/ liegen, sonst wird nichts nachgeladen. Aus den Seiten entsteht ein PDF, das die
JPEG-Bytes unverändert einbettet (DCTDecode); Pillow dekodiert jede Seite nur einmal zur Prüfung (nur JPEG, höchstens
12 Megapixel je Seite, 400 Megapixel und 30 MB insgesamt, 50 Seiten). Der Inhalt geht nie an das Sprachmodell.
"""

from __future__ import annotations

import asyncio
import io
import re
import time
import warnings
from urllib.parse import urlsplit

from PIL import Image

from ..http_klein import HttpFehler, anfrage
from ..konfig import Geraet
from . import Abgelehnt, GeraetFehler, NichtEingerichtet

MAX_SEITE_PIXEL = 12_000_000  # A4 mit 300 dpi sind 8,7 Millionen; Pillow warnt ab dieser Zahl (hier ein Fehler)
Image.MAX_IMAGE_PIXELS = MAX_SEITE_PIXEL
PIXEL_BUDGET = 400_000_000  # alle Seiten eines Scans zusammen (50 Seiten mit 300 dpi)
MAX_SEITEN = 50
MAX_SEITE_BYTES = 25 * 1024 * 1024
MAX_GESAMT_BYTES = 30 * 1024 * 1024  # wie ablage.MAX_DATEI: ein größerer Scan ließe sich nicht ablegen
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
    try:
        teile = urlsplit(location.strip())
        if teile.scheme or teile.netloc:
            if teile.scheme != "http" or teile.hostname != adresse or (teile.port not in (None, 80)):
                raise GeraetFehler("Scanner nennt einen fremden Ort für den Auftrag")
        pfad = teile.path.rstrip("/")
        if not JOB_RE.fullmatch(pfad) or teile.query or teile.fragment:
            raise GeraetFehler("Scanner nennt einen unerwarteten Ort für den Auftrag")
    except ValueError as e:  # urlsplit und .port werfen bei kaputten Adressen ("Invalid IPv6 URL", Port 99999)
        raise GeraetFehler("Scanner nennt einen ungültigen Ort für den Auftrag") from e
    return pfad


def _seite_pruefen(daten: bytes) -> tuple[int, int, str]:
    """(Breite, Höhe, Modus) einer gescannten JPEG-Seite. Nur JPEG in Graustufen oder RGB, höchstens MAX_SEITE_PIXEL;
    die Seite wird einmal vollständig dekodiert (prüft, dass sie wirklich ein JPEG ist) und sofort freigegeben.
    Eine Dekompressionsbombe (Warnung oder Fehler von Pillow) ist ein Fehler."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(daten), formats=["JPEG"]) as im:
                breite, hoehe = im.size
                if breite * hoehe > MAX_SEITE_PIXEL or breite < 1 or hoehe < 1:
                    raise GeraetFehler("Eine gescannte Seite ist größer als erlaubt")
                if im.mode not in ("L", "RGB"):
                    raise GeraetFehler("Scanner liefert ein JPEG in einem unerwarteten Farbmodus")
                modus = im.mode
                im.load()
        except GeraetFehler:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, SyntaxError, ValueError,
                MemoryError) as e:  # OSError enthält UnidentifiedImageError
            raise GeraetFehler("Scanner liefert kein gültiges JPEG") from e
    return breite, hoehe, modus


def pdf_aus_seiten(seiten: list[bytes], aufloesung: int) -> bytes:
    """PDF aus JPEG-Seiten, ohne sie neu zu kodieren: jede Seite ist ein DCT-Bild in einem eigenen Seitenobjekt.
    Speicher: eine Seite zugleich dekodiert (zur Prüfung), sonst nur die Rohbytes. Pixelbudget über alle Seiten."""
    if not seiten:
        raise GeraetFehler("Keine Seite gescannt")
    infos: list[tuple[int, int, str]] = []
    pixel = 0
    for daten in seiten:
        info = _seite_pruefen(daten)
        pixel += info[0] * info[1]
        if pixel > PIXEL_BUDGET:
            raise GeraetFehler("Der Scan ist insgesamt zu groß")
        infos.append(info)
    teile = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    stand = len(teile[0])
    versatz: dict[int, int] = {}

    def objekt(nr: int, rumpf: bytes) -> None:
        nonlocal stand
        versatz[nr] = stand
        stueck = f"{nr} 0 obj\n".encode("ascii") + rumpf + b"\nendobj\n"
        teile.append(stueck)
        stand += len(stueck)

    n = len(seiten)
    kinder = " ".join(f"{3 + 3 * i} 0 R" for i in range(n))
    objekt(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    objekt(2, f"<< /Type /Pages /Kids [{kinder}] /Count {n} >>".encode("ascii"))
    for i, (daten, (w, h, modus)) in enumerate(zip(seiten, infos)):
        seite, inhalt_nr, bild_nr = 3 + 3 * i, 4 + 3 * i, 5 + 3 * i
        bw, bh = w / aufloesung * 72, h / aufloesung * 72
        objekt(seite, (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {bw:.2f} {bh:.2f}] /Resources << /XObject "
                       f"<< /Im0 {bild_nr} 0 R >> >> /Contents {inhalt_nr} 0 R >>").encode("ascii"))
        inhalt = f"q {bw:.2f} 0 0 {bh:.2f} 0 0 cm /Im0 Do Q".encode("ascii")
        objekt(inhalt_nr, f"<< /Length {len(inhalt)} >>\nstream\n".encode("ascii") + inhalt + b"\nendstream")
        farbraum = "/DeviceGray" if modus == "L" else "/DeviceRGB"
        objekt(bild_nr, (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace {farbraum} "
                         f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(daten)} >>\nstream\n").encode("ascii")
               + daten + b"\nendstream")
    anzahl = 3 + 3 * n
    xref = [f"xref\n0 {anzahl}\n".encode("ascii"), b"0000000000 65535 f \n"]
    xref += [f"{versatz[nr]:010d} 00000 n \n".encode("ascii") for nr in range(1, anzahl)]
    ende = f"trailer\n<< /Size {anzahl} /Root 1 0 R >>\nstartxref\n{stand}\n%%EOF\n".encode("ascii")
    return b"".join(teile + xref + [ende])


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
    gesamt = 0
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
            gesamt += len(seite.rumpf)
            if gesamt > MAX_GESAMT_BYTES:
                raise GeraetFehler("Der Scan ist zu groß (mehr als 30 MB). Bitte weniger Seiten oder 150 dpi.")
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
