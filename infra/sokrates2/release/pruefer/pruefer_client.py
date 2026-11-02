"""Client des Annahme-Dienstes der Prüfer-Sandbox (Schritt 1.2c): schickt Basis und neuen Stand und liefert das Ergebnis der Prüfstrecke.

Für den Release-Dienst (Benutzer wamo-release), der die Sandbox nicht selbst starten kann. Nur Standardbibliothek.

    from pruefer_client import pruefen
    erg = pruefen("/pfad/basis", "/pfad/neu", runde=1)     # {"runde": 1, "pruefstand": {...}, "details": {...}, ...}

Auf der Kommandozeile:  python3 pruefer_client.py --basis A --neu B --runde 1     (gibt das Ergebnis als JSON aus, Exit 0 nur, wenn jede Stufe ok ist)

Der Aufruf blockiert, bis die Strecke fertig ist (Minuten). Wirft PrueferFehler, wenn der Dienst nicht antwortet oder ablehnt; der Grund ist ein kurzes
Schlüsselwort (benutzer, kopf, archiv_ungueltig, annahme_fehler, belegt, zeitlimit, lauf_ohne_ergebnis, dienst_nicht_erreichbar, antwort_ungueltig).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import pruefer_archiv as A  # noqa: E402

SOCKET = "/run/wamo-pruefer-annahme/annahme.sock"
ANTWORT_MAX = 4 * 1024 * 1024
SENDEN_ZEIT_S = 120.0
WARTEN_ZEIT_S = 6600.0   # etwas mehr als die Obergrenze eines Laufs im Dienst


class PrueferFehler(Exception):
    def __init__(self, grund: str, detail: str = ""):
        super().__init__(f"{grund}: {detail}" if detail else grund)
        self.grund, self.detail = grund, detail


def pruefen(basis: str, neu: str, runde: int, sockpfad: str = SOCKET, warten_s: float = WARTEN_ZEIT_S) -> dict:
    if runde not in (1, 2, 3):
        raise ValueError("runde muss 1, 2 oder 3 sein")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(SENDEN_ZEIT_S)
    try:
        try:
            s.connect(sockpfad)
        except OSError as e:
            raise PrueferFehler("dienst_nicht_erreichbar", type(e).__name__) from None
        raus = s.makefile("wb")
        try:
            raus.write(f"WAMO-PRUEFEN 1 {runde}\n".encode())
            A.schreibe_archiv(raus, {"basis": basis, "neu": neu})
            raus.flush()
        except (OSError, BrokenPipeError):
            pass   # der Dienst kann schon mit einer Ablehnung geantwortet haben; sie steht in der Antwort
        finally:
            try:
                raus.close()
            except OSError:
                pass
        try:
            s.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        s.settimeout(warten_s)
        daten = b""
        try:
            while len(daten) <= ANTWORT_MAX:
                block = s.recv(65536)
                if not block:
                    break
                daten += block
        except OSError as e:
            raise PrueferFehler("antwort_ungueltig", type(e).__name__) from None
    finally:
        s.close()
    try:
        antwort = json.loads(daten)
    except ValueError:
        raise PrueferFehler("antwort_ungueltig", "kein JSON") from None
    if not isinstance(antwort, dict) or not isinstance(antwort.get("ok"), bool):
        raise PrueferFehler("antwort_ungueltig", "Form")
    if not antwort["ok"]:
        raise PrueferFehler(str(antwort.get("grund", "unbekannt"))[:40], str(antwort.get("detail", ""))[:120])
    ergebnis = antwort.get("ergebnis")
    if not isinstance(ergebnis, dict) or not isinstance(ergebnis.get("pruefstand"), dict):
        raise PrueferFehler("antwort_ungueltig", "Ergebnis")
    return ergebnis


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Prüfstrecke über den Annahme-Dienst starten")
    ap.add_argument("--basis", required=True)
    ap.add_argument("--neu", required=True)
    ap.add_argument("--runde", type=int, required=True, choices=(1, 2, 3))
    ap.add_argument("--socket", default=SOCKET)
    a = ap.parse_args(argv)
    try:
        erg = pruefen(a.basis, a.neu, a.runde, a.socket)
    except PrueferFehler as e:
        print(json.dumps({"ok": False, "grund": e.grund, "detail": e.detail}, ensure_ascii=False))
        return 2
    print(json.dumps(erg, ensure_ascii=False))
    return 0 if all(v == "ok" for v in erg["pruefstand"].values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
