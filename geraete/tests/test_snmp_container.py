"""SNMPv3 gegen einen echten Agenten (net-snmp im Wegwerf-Container). Läuft nur, wenn WAMO_SNMP_TESTZIEL gesetzt ist
(Adresse:Port auf dem eigenen Rechner), sonst wird übersprungen. Der Container hat nur diese Test-Zugangsdaten, nie echte.

Container starten (Docker, höchstens 64 MB), Test laufen lassen, Container beenden:

    docker run -d --name wamo-snmp-test --memory 64m -p 127.0.0.1:16100:161/udp alpine:latest sh -c '
    apk add --no-cache net-snmp >/dev/null 2>&1; mkdir -p /etc/snmp
    printf "createUser wamolese SHA-256 authgeheim123 AES privgeheim456\\nrouser wamolese priv\\nsysName sw-testbuero\\n" \\
      > /etc/snmp/snmpd.conf
    exec snmpd -f -Lo -C -c /etc/snmp/snmpd.conf udp:161'
    WAMO_SNMP_TESTZIEL=127.0.0.1:16100 uv run --python 3.12 pytest -q tests/test_snmp_container.py
    docker rm -f wamo-snmp-test
"""

import os

import pytest

from wamo_geraete.konfig import Geraet, aus_daten
from wamo_geraete.treiber import GeraetFehler, snmp

ZIEL = os.getenv("WAMO_SNMP_TESTZIEL", "")
pytestmark = pytest.mark.skipif(not ZIEL, reason="WAMO_SNMP_TESTZIEL nicht gesetzt (kein SNMP-Testagent)")

GEHEIM = {"WAMO_GERAETE_SW_BENUTZER": "wamolese", "WAMO_GERAETE_SW_PASSWORT": "authgeheim123",
          "WAMO_GERAETE_SW_PRIVPASSWORT": "privgeheim456"}


def geraet(**optionen) -> tuple[Geraet, object]:
    host, port = ZIEL.rsplit(":", 1)
    k = aus_daten({"geraet": [{"kennung": "switch-1", "klasse": "switch", "treiber": "snmp", "adresse": "192.168.178.2",
                               "zugang": "SW", **optionen}]}, GEHEIM)
    echt = k.geraete["switch-1"]
    # Adresse und Port auf den Testagenten umbiegen (die Konfiguration erlaubt nur das Büro-LAN und Port 161)
    return Geraet(echt.kennung, echt.name, echt.klasse, echt.treiber, host, int(port), echt.optionen), k


async def test_status_mit_sha256_und_aes128():
    g, k = geraet(fehlerzaehler=True)
    erg = await snmp.status(g, k)
    assert erg["erreichbar"] is True and erg["name"] == "sw-testbuero"
    assert erg["ports_gesamt"] >= 1 and erg["ports_oben"] >= 1 and erg["ports_oben"] + erg["ports_unten"] <= erg["ports_gesamt"]
    assert erg["fehler_ein"] >= 0 and erg["fehler_aus"] >= 0


async def test_falsches_passwort_wird_abgewiesen_ohne_es_zu_nennen():
    g, k = geraet()
    k.geheimnisse["WAMO_GERAETE_SW_PASSWORT"] = "falschfalsch1"
    with pytest.raises(GeraetFehler) as e:
        await snmp.status(g, k)
    assert "falschfalsch1" not in str(e.value) and "authgeheim123" not in str(e.value)


async def test_falsches_verfahren_wird_abgewiesen():
    g, k = geraet(auth="sha512")
    with pytest.raises(GeraetFehler):
        await snmp.status(g, k)
