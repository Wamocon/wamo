"""Zugang des Release-Dienstes zum Notanker (Konzept D.1 und G, Schritt 0.3).

Der Release-Dienst erreicht den Anker nur über dessen Unix-Socket und nur mit diesen Befehlen:

    status                   Sperre, Zustand, Prüfung des Audit-Protokolls des Ankers
    rueckweg ui|kern|alles   zurück auf den letzten guten Stand (auch mit --trocken)
    sperre an                setzt die Sperre (mit --trocken nur der Plan)
    klassifiziere <id>       Schutzschicht: der Anker ordnet das Diff der Anfrage ein und schreibt Art und Treffer (Schritt 3a)
    bewaehren <id>           eine Probe der Bewährung des umgeschalteten Kerns (Schritt 3c); der Anker entscheidet über bewaehrt und Rückweg
    migrieren|bereitstellen|umschalten <id>   die Vorwärtsbefehle (Schritt 1.4e); der Anker liest die Anfrage und die Freigabe selbst aus der Datenbank

Nie `sperre aus`: Der Anker erlaubt es dem Benutzer wamo-release ohnehin nicht (ERLAUBT im Anker), und dieser Client
kann den Befehl nicht einmal bauen. Die Vorwärtsbefehle tragen nur die Kennung der Anfrage; was sie tun, bestimmt der Anker aus der Datenbank, nie dieser
Client. Eine Erweiterung der Liste ist eine Änderung an diesem Programm. Gegenstelle: Der Socket muss root gehören (SO_PEERCRED), sonst wird nichts gesendet. Antworten sind höchstens
8 KiB, ASCII-JSON und werden streng geprüft; alles andere zählt als "Anker nicht erreichbar" (fail closed).
"""

from __future__ import annotations

import json
import re
import secrets
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Callable

ANTWORT_MAX = 8192
ZEIT_KURZ_S = 5.0  # status und sperre
ZEIT_RUECKWEG_S = {"ui": 150.0, "kern": 200.0, "alles": 330.0}  # die Schritte des Ankers haben 120 und 180 Sekunden
ZEIT_VORWAERTS_S = {"klassifiziere": 60.0, "migrieren": 330.0, "bereitstellen": 420.0, "umschalten": 250.0, "bewaehren": 250.0}   # Migrationen und die Schritte des Kerns dauern
UUID_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")

# Die einzigen Anfragen, die dieser Client senden kann: (Befehl, Argument-Muster, --trocken erlaubt)
ERLAUBT = {
    "status": (None, False),
    "rueckweg": (re.compile(r"ui|kern|alles"), True),
    "sperre": (re.compile(r"an"), True),
    "klassifiziere": (UUID_RE, True),
    "migrieren": (UUID_RE, True),
    "bereitstellen": (UUID_RE, True),
    "umschalten": (UUID_RE, True),
    "bewaehren": (UUID_RE, True),
}


class AnkerFehler(Exception):
    """Der Anker ist nicht erreichbar, hat unsinnig geantwortet oder die Gegenstelle ist nicht root."""

    def __init__(self, grund: str):
        super().__init__(grund)
        self.grund = grund


class Verboten(Exception):
    """Eine Anfrage, die dieser Client nie sendet (zum Beispiel sperre aus). Programmfehler, kein Laufzeitfall."""


@dataclass(frozen=True)
class Antwort:
    ok: bool
    grund: str | None
    daten: dict = field(default_factory=dict)


def peer_uid(sock: socket.socket) -> int | None:
    try:  # struct ucred: pid_t (int), uid_t und gid_t (vorzeichenlos)
        roh = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("iII"))  # type: ignore[attr-defined]
        return struct.unpack("iII", roh)[1]
    except (OSError, AttributeError, struct.error):
        return None


def zeile_bauen(befehl: str, arg: str | None = None, trocken: bool = False) -> bytes:
    """Baut die Anfragezeile oder wirft Verboten. Die Kennung ist für jede Anfrage neu (der Anker bedient keine zweimal)."""
    spez = ERLAUBT.get(befehl)
    if spez is None:
        raise Verboten(f"Befehl {befehl!r} ist nicht erlaubt")
    muster, trocken_ok = spez
    if (muster is None) != (arg is None) or (muster is not None and not muster.fullmatch(arg or "")):
        raise Verboten("Argument nicht erlaubt")
    if trocken and not trocken_ok:
        raise Verboten("--trocken nicht erlaubt")
    teile = [secrets.token_hex(16), befehl]
    if arg is not None:
        teile.append(arg)
    if trocken:
        teile.append("--trocken")
    return (" ".join(teile) + "\n").encode("ascii")


def antwort_pruefen(daten: bytes) -> Antwort:
    if len(daten) > ANTWORT_MAX or not daten.endswith(b"\n") or daten.count(b"\n") != 1:
        raise AnkerFehler("antwort_form")
    try:
        obj = json.loads(daten.decode("ascii"))
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise AnkerFehler("antwort_form") from e
    if not isinstance(obj, dict) or not isinstance(obj.get("ok"), bool):
        raise AnkerFehler("antwort_form")
    grund = obj.get("grund")
    if grund is not None and not (isinstance(grund, str) and re.fullmatch(r"[a-z_]{1,40}", grund)):
        raise AnkerFehler("antwort_form")
    return Antwort(obj["ok"], grund, obj)


class AnkerClient:
    """Eine Verbindung je Anfrage. verbinden und uhr sind nur für die Tests austauschbar."""

    def __init__(self, sockpfad: str = "/run/wamo-anker/anker.sock", erwartete_uid: int = 0,
                 peer: Callable[[socket.socket], int | None] = peer_uid,
                 uhr: Callable[[], float] = time.monotonic):
        self.sockpfad = sockpfad
        self.erwartete_uid = erwartete_uid
        self.peer = peer
        self.uhr = uhr

    def _senden(self, zeile: bytes, zeitlimit: float) -> Antwort:
        familie = getattr(socket, "AF_UNIX", None)
        if familie is None:  # Plattform ohne Unix-Sockets (die Tests unter Windows): es gibt keinen Anker
            raise AnkerFehler("nicht_erreichbar")
        s = socket.socket(familie, socket.SOCK_STREAM)
        s.settimeout(zeitlimit)
        ende = self.uhr() + zeitlimit
        try:
            s.connect(self.sockpfad)
            if self.peer(s) != self.erwartete_uid:  # nur ein Anker von root ist ein Anker
                raise AnkerFehler("gegenstelle")
            s.sendall(zeile)
            s.shutdown(socket.SHUT_WR)
            daten = b""
            while len(daten) <= ANTWORT_MAX:
                if self.uhr() > ende:
                    raise AnkerFehler("zeitlimit")
                teil = s.recv(4096)
                if not teil:
                    break
                daten += teil
        except AnkerFehler:
            raise
        except socket.timeout as e:
            raise AnkerFehler("zeitlimit") from e
        except OSError as e:
            raise AnkerFehler("nicht_erreichbar") from e
        finally:
            s.close()
        return antwort_pruefen(daten)

    def status(self) -> Antwort:
        return self._senden(zeile_bauen("status"), ZEIT_KURZ_S)

    def rueckweg(self, komponente: str, trocken: bool = False) -> Antwort:
        zeile = zeile_bauen("rueckweg", komponente, trocken)
        return self._senden(zeile, ZEIT_RUECKWEG_S[komponente])

    def vorwaerts(self, befehl: str, anfrage: str, trocken: bool = False) -> Antwort:
        """klassifiziere, migrieren, bereitstellen oder umschalten für genau eine Anfrage. Der Anker prüft alles Weitere selbst."""
        if befehl not in ZEIT_VORWAERTS_S:
            raise Verboten(f"Befehl {befehl!r} ist kein Vorwärtsbefehl")
        return self._senden(zeile_bauen(befehl, anfrage, trocken), ZEIT_VORWAERTS_S[befehl])

    def sperre_an(self, trocken: bool = False) -> Antwort:
        return self._senden(zeile_bauen("sperre", "an", trocken), ZEIT_KURZ_S)

    def sperre_zustand(self) -> str:
        """"an", "aus", "defekt" oder "unbekannt" (Anker nicht erreichbar oder Antwort ohne verwertbare Sperre). Nur "aus"
        lässt Selbständerung zu."""
        try:
            a = self.status()
        except AnkerFehler:
            return "unbekannt"
        wert = a.daten.get("sperre")
        return wert if a.ok and wert in ("an", "aus", "defekt") else "unbekannt"
