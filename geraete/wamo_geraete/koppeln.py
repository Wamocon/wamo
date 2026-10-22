"""Einmalige Kopplung eines Fernsehers (LG webOS oder Samsung; nie automatisch, nie über den Kern).

    sudo -u wamo-geraete WAMO_GERAETE_KONFIG=/etc/wamo-geraete/geraete.toml \\
        /opt/wamo-geraete/venv/bin/python -m wamo_geraete.koppeln <kennung>

Der Fernseher muss an sein. Er zeigt "WAMO zulassen?"; die Person am Fernseher bestätigt mit der Fernbedienung
(30 Sekunden Zeit). Danach liegen Token und Zertifikats-Fingerabdruck in /var/lib/wamo-geraete/kopplung/<kennung>.json.
Rückweg: diese Datei löschen und am Fernseher die Kopplung entfernen (LG: Einstellungen > Allgemein > Externe Geräte >
Verbundene Geräte verwalten; Samsung: Externe Geräteverwaltung > Geräteverbindungs-Manager)."""

from __future__ import annotations

import asyncio
import sys

from .konfig import KonfigFehler, lade
from .treiber import fernseher, lg_tv


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Aufruf: python -m wamo_geraete.koppeln <kennung>", file=sys.stderr)
        return 2
    try:
        konfig = lade()
    except KonfigFehler as e:
        print(f"Konfiguration: {e}", file=sys.stderr)
        return 1
    geraet = konfig.geraete.get(argv[1])
    if geraet is None or geraet.treiber not in ("samsung-tv", "lg-webos") or not geraet.adresse:
        print("Kein Fernseher (samsung-tv, lg-webos) mit Adresse unter dieser Kennung", file=sys.stderr)
        return 1
    print("Bitte jetzt am Fernseher 'WAMO zulassen' bestätigen (30 Sekunden) ...")
    try:
        modul = fernseher if geraet.treiber == "samsung-tv" else lg_tv
        fingerabdruck = asyncio.run(modul.koppeln(geraet))
    except Exception as e:  # noqa: BLE001 (Meldung für den Admin, ohne Token)
        print(f"Kopplung fehlgeschlagen: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        return 1
    print(f"Gekoppelt. Zertifikat des Fernsehers: {fingerabdruck}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
