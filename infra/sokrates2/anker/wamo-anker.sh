#!/bin/sh
# Aufruf des WAMO-Notankers (Client). Installiert nach /usr/local/lib/wamo-anker (root:root) und als
# /usr/local/bin/wamo-anker verlinkt. Der Pfad ist fest, es wird nichts aus der Umgebung übernommen
# (python3 -I ignoriert PYTHON*-Variablen und das Arbeitsverzeichnis).
exec /usr/bin/python3 -I /usr/local/lib/wamo-anker/wamo_anker.py "$@"
