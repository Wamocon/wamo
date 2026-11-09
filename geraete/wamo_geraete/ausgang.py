"""Ausgangsfilter aus geraete.toml erzeugen (für wamo-geraete-einrichten.sh, läuft als root mit python -I).

    python -m wamo_geraete.ausgang nft <uid>     nft-Regeln für die Tabelle inet wamo_geraete_egress
    python -m wamo_geraete.ausgang erlauben      Zeile IPAddressAllow=... für die Unit (Drop-in)

Eine Quelle für beide Schichten: die Geräte mit Adresse und Port aus der Konfiguration (TCP je Gerät, UDP nur für SNMPv3
auf Port 161 zu genau der Adresse des Switches oder Access Points und für Wake-on-LAN an die Broadcast-Adresse). Alles andere (Internet, andere
LAN-Adressen, Loopback, IPv6, andere Ports) weist die Tabelle ab, systemd sperrt es zusätzlich per IPAddressDeny=any.
"""

from __future__ import annotations

import sys

from .konfig import KonfigFehler, Konfig, lade


def nft(konfig: Konfig, uid: int) -> str:
    regeln = []
    for proto, adresse, port in konfig.ziele():
        regeln.append(f"\t\tip daddr {adresse} {proto} dport {port} accept")
    return "\n".join([
        "#!/usr/sbin/nft -f",
        "# WAMO-Gerätedienst: Ausgang nur zu den Geräten aus /etc/wamo-geraete/geraete.toml (erzeugt von",
        "# wamo-geraete-einrichten.sh, nicht von Hand ändern). Nur Sockets des Benutzers wamo-geraete laufen hier durch.",
        "table inet wamo_geraete_egress",
        "delete table inet wamo_geraete_egress",
        "",
        "table inet wamo_geraete_egress {",
        "\tchain ausgang {",
        "\t\ttype filter hook output priority 0; policy accept;",
        f"\t\tmeta skuid {int(uid)} counter jump geraete",
        "\t}",
        "",
        "\tchain geraete {",
        "\t\tct state established,related accept",
        *regeln,
        "\t\tcounter jump abweisen",
        "\t}",
        "",
        "\tchain abweisen {",
        '\t\tlimit rate 6/minute burst 10 packets log prefix "WAMO-GERAETE abgewiesen: " level warn',
        "\t\treject with icmpx type admin-prohibited",
        "\t}",
        "}",
        "",
    ])


def erlauben(konfig: Konfig) -> str:
    adressen = set(konfig.adressen())
    if any(proto == "udp" and adresse.endswith(".255") for proto, adresse, _ in konfig.ziele()):
        adressen.add("192.168.178.255")
    return "IPAddressAllow=" + " ".join(sorted(adressen)) if adressen else "IPAddressAllow="


def main(argv: list[str]) -> int:
    try:
        konfig = lade()
    except KonfigFehler as e:
        print(f"Konfiguration: {e}", file=sys.stderr)
        return 1
    if len(argv) == 3 and argv[1] == "nft" and argv[2].isdigit():
        sys.stdout.write(nft(konfig, int(argv[2])))
        return 0
    if len(argv) == 2 and argv[1] == "erlauben":
        print(erlauben(konfig))
        return 0
    if len(argv) == 2 and argv[1] == "pruefen":
        ziele = konfig.ziele()
        print(f"{len(konfig.geraete)} Geräte, {len(ziele)} Ziele, davon {sum(1 for z in ziele if z[0] == 'udp')} UDP "
              f"(SNMP 161, Wake-on-LAN 9)")
        return 0
    print("Aufruf: python -m wamo_geraete.ausgang nft <uid> | erlauben | pruefen", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
