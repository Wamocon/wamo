"""Start des Release-Dienstes mit den echten Stufen (Schritt 3b).

Der Kern (wamo_release.py) kennt nur die Schnittstelle der Stufen und importiert weder Git noch die Sandbox. Dieses Modul baut die echten Stufen und übergibt sie
dem Kern. Es gibt drei Fälle, und in keinem stürzt der Dienst ab:

  Quelle eingerichtet (/etc/wamo-release/quelle.env)   EchteStufen: lokale Quelle, Prüfer-Sandbox, Anker
  keine Quelle oder ein Fehler beim Aufbau            NichtVerfuegbar: jede Stufe meldet es ehrlich, der Dienst beendet die Anfrage mit stufe_nicht_verfuegbar

Andere Aufrufe als "dienst" gehen unverändert an den Kern (Client, Selbstprüfung).
"""

from __future__ import annotations

import sys

QUELLE_DATEI = "/etc/wamo-release/quelle.env"


def stufen_bauen():
    """Die Stufen für den Betrieb. Gibt NichtVerfuegbar zurück, wenn es keine Quelle gibt oder der Aufbau scheitert (mit einer Zeile ohne Einzelheiten im Journal)."""
    from release_stufen import NichtVerfuegbar
    try:
        import wamo_release as W
        from quelle import quelle_aus_datei
        from release_anker import AnkerClient
        from stufen_echt import EchteStufen

        quelle = quelle_aus_datei(QUELLE_DATEI)
        if quelle is None:
            print("Keine Quelle eingerichtet, die Stufen sind nicht verfügbar.", file=sys.stderr)
            return NichtVerfuegbar()
        # Die Funde der Wirkungsflächen für die Karte liegen als kleine Dateien im Zustandsordner (reine Anzeige, außerhalb der Hashkette)
        return EchteStufen(quelle, AnkerClient(W.PFADE.anker_socket), anzeige_ordner=W.PFADE.zustand + "/anzeige")
    except Exception as e:  # noqa: BLE001  jede Panne beim Aufbau heißt "keine Stufen", nie ein Absturz des Dienstes
        print(f"Stufen nicht aufgebaut ({type(e).__name__}).", file=sys.stderr)
        return NichtVerfuegbar()


def main(argv: list[str]) -> int:
    import wamo_release as W
    if argv[1:] == ["dienst"]:
        return W.dienst_main(stufen_bauen())
    return W.main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
