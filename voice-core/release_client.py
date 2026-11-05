"""Client des Kerns zum Release-Dienst (Selbstüberarbeitung, Schritt 1.5, Konzept D.1 und D.5). Nur Standardbibliothek.

Der Kern darf dem Release-Dienst nur zwei Dinge sagen: den Stand lesen (status) und eine Anfrage vorschlagen (anfordern). Dieses Modul kann bisher nur
den Stand lesen; Vorschlagen kommt mit den Werkzeugen in Schritt 3.4. Freigeben kann der Kern nie, das tut allein der Freigabe-Dienst des Ankers.

Der Weg: Unix-Socket /run/wamo-release/ctl.sock (Gruppe wamo-core, der Dienst prüft die Gegenstelle per SO_PEERCRED), eine Zeile je Verbindung
"<Kennung> status", Antwort eine JSON-Zeile. Die Kennung ist je Anfrage neu (der Dienst lehnt eine wiederholte ab).

ansicht() macht aus der Antwort eine kleine, geprüfte Sicht für die Oberfläche: nur bekannte Felder, Texte gekürzt und ohne Steuerzeichen, Zahlen und
Wahrheitswerte nur als solche. Was der Dienst sonst noch meldet (Hashes der Audit-Kette, Pfade, Probleme der Installation im Wortlaut), bekommt die
Oberfläche nicht.

Vertrag mit der Oberfläche (Route GET /wamo/api/release/status, Schritt 1.5). Die Sicht hat die Felder verfuegbar, modus, version, bereit, defekt, angehalten,
anker, datenbank, arbeiter, audit_kette, installation, abgeschlossen, zeit und anfrage (oder null). anfrage hat zusätzlich zu id, status, quelle, schritt,
schritte, seit, frist_bis, versuch, fertig, version und klasse:

    v_commit        40 Hex-Zeichen oder null (der Stand V, der freigegeben wird)
    tree_id         40 Hex-Zeichen oder null (der Tree von V, an den die Freigabe gebunden ist)
    von_mir         bool, ob die Anfrage vom angemeldeten Profil stammt (die Route bildet es aus profil und entfernt profil; der Client liefert profil als UUID oder null)
    treffer_anzahl  int 0 bis 200 (Funde der Schutzschicht; 0, wenn der Dienst noch nichts meldet)
    pruefstand      {name: "ok" | "fehler" | "uebersprungen"}, höchstens 16 Einträge, sonst {}
    pruefung        null oder, nur in den Zuständen pruefung3 und wartet_freigabe, ein Block (die Route liefert ihn, das Sprachwerkzeug nie):
                    {diff:       {anzahl, hinzu, weg, binaer, gekappt, dateien: [{s, p, a, d}]},
                     wirkung:    {anzahl, nach_art: {art: n}, sonder_hinweis, neue_pakete, windows, treffer: [{p, z, a, d}]},
                     empfehlung: {stufe: "keine" | "patch" | "minor" | "major", version, feat, fix, sonst, unkonventionell, major_null}}
                    diff: anzahl, hinzu, weg, binaer sind Ganzzahlen; gekappt ist bool; dateien hat höchstens 20 Einträge: s ist A, M, D oder T, p ein Pfad (nur ASCII,
                    höchstens 70 Zeichen), a und d sind Zeilenzahlen oder null bei Binärdateien.
                    wirkung: nach_art zählt die Funde je Art (prozess, auswerten, serialisierung, netzcode, sudo, netzziel, geheimnisname), sonder_hinweis ist bool (ein
                    Geheimnisname außerhalb seines Moduls), neue_pakete eine Zahl, windows bool (Änderungen in host/ oder desktop/, die unter Linux nicht laufen);
                    treffer hat höchstens 15 Einträge: p Pfad, z Zeile, a Art, d Detail (ein Rechnername oder Geheimnisname, nie gefundener Text).
                    empfehlung: reine Warnung nach Konzept F.2 bis F.4, kein Tor; version ist die empfohlene Nummer oder "", major_null warnt vor dem Sprung von
                    0.y.z auf 1.0.0.
    Fehlt ein Teil (der Dienst ist älter oder meldet ihn nicht), sind die Felder null, {} oder 0; die Rohantwort des Dienstes wird nie durchgereicht, jedes Feld
    wird hier einzeln geprüft (Wortlisten, Obergrenzen, Texte gekappt und auf ASCII gebracht), eine falsche Form fällt auf null.
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


def status(sockpfad: str | None = None, zeit: float = ZEIT_S, mit_pruefung: bool = False) -> dict:
    """mit_pruefung=True nur für die HTTP-Route der Oberfläche; ein Sprachwerkzeug ruft status() ohne und bekommt den Block pruefung (Pfade, Funde) nie."""
    return ansicht(anfrage("status", sockpfad, zeit), mit_pruefung)


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


COMMIT_RE = re.compile(r"[0-9a-f]{40}")
ASCII_UNERLAUBT_RE = re.compile(r"[^A-Za-z0-9 _.:/=,+@-]")
PRUEFSTAND_NAME_RE = re.compile(r"[a-z][a-z0-9_]{1,23}")
PRUEFSTAND_WERTE = ("ok", "fehler", "uebersprungen")
VERSION_RE = re.compile(r"\d{1,9}\.\d{1,9}\.\d{1,9}")
DATEI_STATUS = ("A", "M", "D", "T")
TREFFER_ARTEN = ("prozess", "auswerten", "serialisierung", "netzcode", "sudo", "netzziel", "geheimnisname")
EMPFEHLUNG_STUFEN = ("keine", "patch", "minor", "major")
DATEIEN_MAX = 20
TREFFER_MAX = 15
ZEILEN_MAX = 999_999
SUMME_MAX = 99_999_999
ZAEHLER_MAX = 99_999


def _commit(wert: object) -> str | None:
    """Genau 40 Hex-Zeichen (klein) oder None."""
    return wert if isinstance(wert, str) and COMMIT_RE.fullmatch(wert) else None


def _ascii(wert: object, n: int) -> str:
    """Nur harmlose ASCII-Zeichen, gekappt (alles andere, auch Steuer- und Bidi-Zeichen, wird zu ?)."""
    return ASCII_UNERLAUBT_RE.sub("?", wert)[:n] if isinstance(wert, str) else ""


def _bool(wert: object) -> bool | None:
    return wert if isinstance(wert, bool) else None


def _pruefstand(wert: object) -> dict[str, str]:
    if not isinstance(wert, dict):
        return {}
    erg = {}
    for k, v in wert.items():
        if isinstance(k, str) and PRUEFSTAND_NAME_RE.fullmatch(k) and v in PRUEFSTAND_WERTE and len(erg) < 16:
            erg[k] = v
    return erg


def _diff(d: object) -> dict | None:
    if not isinstance(d, dict):
        return None
    zahlen = {k: _zahl(d.get(k), SUMME_MAX) for k in ("anzahl", "hinzu", "weg", "binaer")}
    gekappt = _bool(d.get("gekappt"))
    if gekappt is None or any(v is None for v in zahlen.values()) or not isinstance(d.get("dateien"), list):
        return None
    dateien = []
    for e in d["dateien"][:DATEIEN_MAX]:
        if not (isinstance(e, dict) and e.get("s") in DATEI_STATUS and isinstance(e.get("p"), str)):
            continue
        a, z = e.get("a"), e.get("d")
        if not ((a is None or _zahl(a, ZEILEN_MAX) is not None) and (z is None or _zahl(z, ZEILEN_MAX) is not None)):
            continue
        dateien.append({"s": e["s"], "p": _ascii(e["p"], 70), "a": a, "d": z})
    return {**zahlen, "gekappt": gekappt, "dateien": dateien}


def _wirkung(w: object) -> dict | None:
    if not isinstance(w, dict):
        return None
    anzahl, pakete = _zahl(w.get("anzahl"), 200), _zahl(w.get("neue_pakete"), 200)
    sonder, windows = _bool(w.get("sonder_hinweis")), _bool(w.get("windows"))
    if None in (anzahl, pakete, sonder, windows) or not isinstance(w.get("nach_art"), dict) or not isinstance(w.get("treffer"), list):
        return None
    nach_art = {k: n for k, v in w["nach_art"].items() if k in TREFFER_ARTEN and (n := _zahl(v, ZAEHLER_MAX)) is not None}
    treffer = []
    for e in w["treffer"][:TREFFER_MAX]:
        if isinstance(e, dict) and e.get("a") in TREFFER_ARTEN and isinstance(e.get("p"), str) and _zahl(e.get("z"), ZEILEN_MAX) is not None:
            treffer.append({"p": _ascii(e["p"], 70), "z": e["z"], "a": e["a"], "d": _ascii(e.get("d"), 40)})
    return {"anzahl": anzahl, "nach_art": nach_art, "sonder_hinweis": sonder, "neue_pakete": pakete, "windows": windows, "treffer": treffer}


def _empfehlung(e: object) -> dict | None:
    if not isinstance(e, dict) or e.get("stufe") not in EMPFEHLUNG_STUFEN:
        return None
    version, major_null = e.get("version"), _bool(e.get("major_null"))
    zaehler = {k: _zahl(e.get(k), ZAEHLER_MAX) for k in ("feat", "fix", "sonst", "unkonventionell")}
    if major_null is None or any(v is None for v in zaehler.values()) or not (version == "" or (isinstance(version, str) and VERSION_RE.fullmatch(version))):
        return None
    return {"stufe": e["stufe"], "version": version, **zaehler, "major_null": major_null}


def _pruefung(p: object) -> dict | None:
    """Der Block pruefung des Dienstes in strenger Form oder None. Jeder Teil wird einzeln geprüft; ein falsch geformter Teil wird zu None, die Rohantwort geht nie durch."""
    if not isinstance(p, dict):
        return None
    block = {"diff": _diff(p.get("diff")), "wirkung": _wirkung(p.get("wirkung")), "empfehlung": _empfehlung(p.get("empfehlung"))}
    return block if any(v is not None for v in block.values()) else None


def _anfrage(a: object, mit_pruefung: bool = False) -> dict | None:
    if not isinstance(a, dict):
        return None
    kennung = a.get("id")
    if not (isinstance(kennung, str) and KENNUNG_RE.fullmatch(kennung)):
        return None
    version = a.get("version")
    profil = a.get("profil")
    return {"id": kennung, "status": _wort(a.get("status")), "quelle": _wort(a.get("quelle"), QUELLEN), "schritt": _zahl(a.get("schritt"), 40),
            "schritte": _zahl(a.get("schritte"), 40), "seit": _text(a.get("seit"), 40), "frist_bis": _text(a.get("frist_bis"), 40),
            "versuch": _zahl(a.get("versuch"), 100), "fertig": a.get("fertig") is True,
            "version": version if isinstance(version, str) and re.fullmatch(r"\d{1,6}\.\d{1,6}\.\d{1,6}", version) else None,
            "klasse": _wort(a.get("klasse")),
            "v_commit": _commit(a.get("v_commit")), "tree_id": _commit(a.get("tree_id")),
            "profil": profil if isinstance(profil, str) and KENNUNG_RE.fullmatch(profil) else None,   # nur für die Route, die daraus von_mir bildet und es entfernt
            "treffer_anzahl": _zahl(a.get("treffer_anzahl"), 200) or 0, "pruefstand": _pruefstand(a.get("pruefstand")),
            "pruefung": _pruefung(a.get("pruefung")) if mit_pruefung else None}


def ansicht(antwort: dict, mit_pruefung: bool = False) -> dict:
    """Die Sicht für die Oberfläche. Unbekannte oder falsch geformte Felder fallen weg oder werden zu "unbekannt". Der Block pruefung (Pfade, Funde) kommt nur
    mit mit_pruefung=True hinein, also nur über die HTTP-Route."""
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
        "anfrage": _anfrage(antwort.get("anfrage"), mit_pruefung),
        "abgeschlossen": fertig[-5:],
        "zeit": _text(antwort.get("zeit"), 40),
    }
