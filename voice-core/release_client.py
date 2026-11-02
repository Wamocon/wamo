"""Client des Kerns zum Release-Dienst (Selbstüberarbeitung, Schritt 1.5, Konzept D.1 und D.5). Nur Standardbibliothek.

Der Kern darf dem Release-Dienst nur zwei Dinge sagen: den Stand lesen (status) und eine Anfrage vorschlagen (anfordern). Dieses Modul kann bisher nur
den Stand lesen; Vorschlagen kommt mit den Werkzeugen in Schritt 3.4. Freigeben kann der Kern nie, das tut allein der Freigabe-Dienst des Ankers.

Der Weg: Unix-Socket /run/wamo-release/ctl.sock (Gruppe wamo-core, der Dienst prüft die Gegenstelle per SO_PEERCRED), eine Zeile je Verbindung
"<Kennung> status", Antwort eine JSON-Zeile. Die Kennung ist je Anfrage neu (der Dienst lehnt eine wiederholte ab).

ansicht() macht aus der Antwort eine kleine, geprüfte Sicht für die Oberfläche: nur bekannte Felder, Texte gekürzt und ohne Steuerzeichen, Zahlen und
Wahrheitswerte nur als solche. Was der Dienst sonst noch meldet (Hashes der Audit-Kette, Pfade, Probleme der Installation im Wortlaut), bekommt die
Oberfläche nicht.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket

SOCKET_STANDARD = "/run/wamo-release/ctl.sock"
ANTWORT_MAX = 64 * 1024
ZEIT_S = 5.0
KENNUNG_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
STATUS_WORT_RE = re.compile(r"[a-z][a-z0-9_]{0,39}")
SPERREN = ("an", "aus", "unbekannt", "defekt")
DATENBANK = ("angeschlossen", "fehlt", "nicht_erreichbar")
ARBEITER = ("frei", "beschaeftigt", "haengt")
QUELLEN = ("selbst", "main")


class ReleaseFehler(Exception):
    """Kurzer Grund als Schlüsselwort (nie ein Text aus der Antwort des Dienstes)."""

    def __init__(self, grund: str):
        super().__init__(grund)
        self.grund = grund


def socket_pfad() -> str:
    return os.getenv("WAMO_RELEASE_SOCKET") or SOCKET_STANDARD


def anfrage(befehl: str, sockpfad: str | None = None, zeit: float = ZEIT_S) -> dict:
    """Eine Anfrage an ctl.sock. Wirft ReleaseFehler (nicht_verfuegbar, kein_zugang, keine_antwort, antwort_ungueltig, abgelehnt)."""
    if not re.fullmatch(r"[a-z][a-z-]{1,19}", befehl):
        raise ValueError("befehl")
    if not hasattr(socket, "AF_UNIX"):
        raise ReleaseFehler("nicht_verfuegbar")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(zeit)
    try:
        try:
            s.connect(sockpfad or socket_pfad())
        except FileNotFoundError:
            raise ReleaseFehler("nicht_verfuegbar") from None
        except PermissionError:
            raise ReleaseFehler("kein_zugang") from None
        except OSError:
            raise ReleaseFehler("nicht_verfuegbar") from None
        s.sendall(f"{secrets.token_hex(16)} {befehl}\n".encode("ascii"))
        s.shutdown(socket.SHUT_WR)
        daten = b""
        try:
            while len(daten) <= ANTWORT_MAX:
                block = s.recv(8192)
                if not block:
                    break
                daten += block
        except OSError:
            raise ReleaseFehler("keine_antwort") from None
    finally:
        s.close()
    if not daten or len(daten) > ANTWORT_MAX:
        raise ReleaseFehler("keine_antwort")
    try:
        antwort = json.loads(daten)
    except ValueError:
        raise ReleaseFehler("antwort_ungueltig") from None
    if not isinstance(antwort, dict) or not isinstance(antwort.get("ok"), bool):
        raise ReleaseFehler("antwort_ungueltig")
    if not antwort["ok"]:
        grund = antwort.get("grund")
        raise ReleaseFehler(grund if isinstance(grund, str) and STATUS_WORT_RE.fullmatch(grund) else "abgelehnt")
    return antwort


def status(sockpfad: str | None = None, zeit: float = ZEIT_S) -> dict:
    return ansicht(anfrage("status", sockpfad, zeit))


# ------------------------------------------------------------------------------------------------ geprüfte Sicht
def _wort(wert: object, erlaubt: tuple[str, ...] | None = None, standard: str | None = None) -> str | None:
    if isinstance(wert, str) and STATUS_WORT_RE.fullmatch(wert) and (erlaubt is None or wert in erlaubt):
        return wert
    return standard


def _text(wert: object, n: int = 80) -> str | None:
    if not isinstance(wert, str):
        return None
    sauber = "".join(c if c.isprintable() else " " for c in wert).strip()
    return sauber[:n] or None


def _zahl(wert: object, hoechstens: int = 10_000) -> int | None:
    return wert if isinstance(wert, int) and not isinstance(wert, bool) and 0 <= wert <= hoechstens else None


def _anfrage(a: object) -> dict | None:
    if not isinstance(a, dict):
        return None
    kennung = a.get("id")
    if not (isinstance(kennung, str) and KENNUNG_RE.fullmatch(kennung)):
        return None
    version = a.get("version")
    return {"id": kennung, "status": _wort(a.get("status")), "quelle": _wort(a.get("quelle"), QUELLEN), "schritt": _zahl(a.get("schritt"), 40),
            "schritte": _zahl(a.get("schritte"), 40), "seit": _text(a.get("seit"), 40), "frist_bis": _text(a.get("frist_bis"), 40),
            "versuch": _zahl(a.get("versuch"), 100), "fertig": a.get("fertig") is True,
            "version": version if isinstance(version, str) and re.fullmatch(r"\d{1,6}\.\d{1,6}\.\d{1,6}", version) else None,
            "klasse": _wort(a.get("klasse"))}


def ansicht(antwort: dict) -> dict:
    """Die Sicht für die Oberfläche. Unbekannte oder falsch geformte Felder fallen weg oder werden zu "unbekannt"."""
    anker = antwort.get("anker") if isinstance(antwort.get("anker"), dict) else {}
    audit = antwort.get("audit") if isinstance(antwort.get("audit"), dict) else {}
    fertig = []
    for e in antwort.get("abgeschlossen") if isinstance(antwort.get("abgeschlossen"), list) else []:
        if isinstance(e, dict) and isinstance(e.get("id"), str) and KENNUNG_RE.fullmatch(e["id"]):
            fertig.append({"id": e["id"], "status": _wort(e.get("status")), "grund": _wort(e.get("grund"))})
    return {
        "verfuegbar": True,
        "modus": _wort(antwort.get("modus"), standard="unbekannt"),
        "version": _text(antwort.get("version"), 20),
        "bereit": antwort.get("bereit") is True,
        "defekt": _text(antwort.get("defekt"), 80),
        "angehalten": _text(antwort.get("angehalten"), 80) if antwort.get("angehalten") else None,
        "anker": {"erreichbar": anker.get("erreichbar") is True, "sperre": _wort(anker.get("sperre"), SPERREN, "unbekannt")},
        "datenbank": _wort(antwort.get("datenbank"), DATENBANK, "unbekannt"),
        "arbeiter": _wort(antwort.get("arbeiter"), ARBEITER, "unbekannt"),
        "audit_kette": "ok" if audit.get("kette") == "ok" else "defekt",
        "installation": "ok" if antwort.get("installation") == "ok" else "abweichung",
        "anfrage": _anfrage(antwort.get("anfrage")),
        "abgeschlossen": fertig[-5:],
        "zeit": _text(antwort.get("zeit"), 40),
    }
