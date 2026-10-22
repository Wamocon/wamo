#!/bin/sh
# Aufruf des WAMO-Release-Dienstes (Client). Installiert nach /opt/wamo-release/app (root:root) und als
# /usr/local/bin/wamo-release verlinkt. Der Pfad ist fest, es wird nichts aus der Umgebung übernommen
# (python3 -E ignoriert PYTHON*-Variablen, -s das Benutzerverzeichnis für Pakete). Der Dienst läuft nur für root und den
# Kern (wamo-core): als Mensch `sudo wamo-release status`.
exec /usr/bin/python3 -E -s -B /opt/wamo-release/app/wamo_release.py "$@"
