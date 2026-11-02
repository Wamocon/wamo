"""Dienst der Migrationsprobe (Schritt 2.5b, Konzept docs/selbstueberarbeitung.md Abschnitt C).

Eigener kleiner Prozess, weil nur er an den Docker-Socket darf. Der Annahme-Dienst liest unvertraute Archive und bleibt deshalb ohne Docker;
er ruft diesen Dienst auf und nennt nur den NAMEN seines Arbeitsordners. Socket-Aktivierung (wamo-migprobe.socket, Accept=yes, eine Verbindung
je Prozess, Modus 0600 root). Eine Zeile rein, eine Zeile Antwort:

    MIGPROBE <ordner>\\n      ordner passt auf a.XXXXXXXX und liegt unter /srv/wamo-pruefer/annahme (root-eigen, kein Link)
    {"wert": "ok"|"fehler", "fehler": "<kurzer Code>"}\\n

Die Probe selbst (migration_probe.py) startet den Wegwerf-Container ohne Netz. Antwort nie mit SQL-Text oder Servermeldung.
"""

from __future__ import annotations

import json
import os
import re
import socket
import stat
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import migration_probe as MP  # noqa: E402

ANNAHME = "/srv/wamo-pruefer/annahme"
ZEILE_RE = re.compile(rb"MIGPROBE (a\.[A-Za-z0-9_]{4,16})\n")
LESE_MAX = 64


def peer_uid(conn: socket.socket) -> int | None:
    try:
        _pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        return uid
    except (OSError, AttributeError, struct.error):
        return None


def _antwort(raus, wert: str, fehler: str = "") -> None:
    raus.write((json.dumps({"wert": wert, "fehler": fehler}) + "\n").encode("ascii"))
    raus.flush()


def bedienen(rein, raus, peer: int | None, annahme: str = ANNAHME, probe=MP.probe, root_uid: int = 0) -> int:
    """Eine Anfrage. Der Rückgabewert ist immer 0, sobald geantwortet wurde (sonst bliebe die Instanz bei systemd als "failed" liegen);
    der Fehler steht in der Antwort. Gibt auch bei Fehlern nur kurze Codes zurück."""
    if peer != 0:
        _antwort(raus, "fehler", "benutzer")
        return 0
    zeile = rein.readline(LESE_MAX)
    m = ZEILE_RE.fullmatch(zeile)
    if not m:
        _antwort(raus, "fehler", "form")
        return 0
    ordner = os.path.join(annahme, m.group(1).decode("ascii"))
    try:
        st = os.lstat(ordner)
    except OSError:
        _antwort(raus, "fehler", "ordner_fehlt")
        return 0
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != root_uid or (st.st_mode & 0o022):
        _antwort(raus, "fehler", "ordner_unsicher")
        return 0
    neu = os.path.join(ordner, "neu")
    if os.path.islink(neu) or not os.path.isdir(neu):
        _antwort(raus, "fehler", "baum_fehlt")
        return 0
    try:
        erg = probe(neu, ordner)
    except Exception as e:  # noqa: BLE001  eine Panne der Probe ist ein Fehler der Stufe, nie ein Absturz
        _antwort(raus, "fehler", "panne_" + type(e).__name__.lower()[:20])
        return 0
    wert = "ok" if erg.get("wert") == "ok" else "fehler"
    fehler = "" if wert == "ok" else re.sub(r"[^a-z0-9_:]", "", str(erg.get("fehler", "frisch"))[:80]) or "frisch"
    _antwort(raus, wert, fehler)
    return 0


def main() -> int:
    conn = socket.socket(fileno=0)
    with conn:
        conn.settimeout(10.0)
        peer = peer_uid(conn)
        with conn.makefile("rb") as rein, conn.makefile("wb") as raus:
            return bedienen(rein, raus, peer)


if __name__ == "__main__":
    sys.exit(main())
