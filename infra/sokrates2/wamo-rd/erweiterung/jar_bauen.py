#!/usr/bin/env python3
"""Baut die Guacamole-Erweiterung wamo-eingabe.jar aus den Dateien dieses Ordners (ein Jar ist ein ZIP mit guac-manifest.json).

Aufruf: python3 jar_bauen.py <Zieldatei>. Das Ergebnis hängt nur vom Inhalt ab (feste Zeitstempel und Reihenfolge), so lässt sich
die Datei vergleichen und nur bei einer Änderung neu installieren."""
import sys
import zipfile
from pathlib import Path

DATEIEN = ("guac-manifest.json", "wamo-eingabe.js", "wamo-eingabe.css")


def bauen(ziel: Path, quelle: Path | None = None) -> None:
    quelle = quelle or Path(__file__).resolve().parent
    with zipfile.ZipFile(ziel, "w", zipfile.ZIP_DEFLATED) as z:
        for name in DATEIEN:
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 4, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, (quelle / name).read_bytes())


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Aufruf: jar_bauen.py <Zieldatei>")
    bauen(Path(sys.argv[1]))
