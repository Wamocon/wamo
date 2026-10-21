"""WAMO-Gerätedienst (wamo-geraete) auf Sokrates-2.

Ein schlanker Dienst unter eigenem Benutzer, der die gemeinsamen Bürogeräte für den Kern (wamo-core) erreicht:
Server-Status, Drucker, Scanner, Router, NAS, Fernseher, Lautsprecher, Steckdosen. Der Kern spricht ihn über den
Unix-Socket /run/wamo-geraete/geraete.sock an (eine JSON-Anfrage, eine JSON-Antwort je Verbindung, siehe server.py).

Warum ein eigener Dienst: Er ist die Ausgangsgrenze (nur feste Adressen und Ports, systemd IPAddressAllow und eine
eigene nft-Tabelle), isoliert die Parser für Geräteantworten (IPP, eSCL, SOAP, SMB) vom Kern und hält die einzigen
Zugangsdaten für Geräte (FRITZ!Box, NAS, Fernseher, SSH-Schlüssel für die Sparks). Er kennt keine Gespräche.
"""

__version__ = "0.1.0"
