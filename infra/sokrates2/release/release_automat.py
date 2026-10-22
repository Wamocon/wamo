"""Zustandsautomat einer Release-Anfrage (Konzept docs/selbstueberarbeitung.md D.1 und E.1, Schritt 0.3).

Reine Logik ohne Ein- und Ausgabe: Zustände, Übergangstabelle, Zeitgrenzen, Prüfung der Stufenergebnisse und das Anwenden
einer Zeile des Write-Ahead-Logs auf den Zustand. Der Dienst (wamo_release.py) schreibt zuerst die Zeile ins Audit-Protokoll
und ruft dann anwenden(); nach einem Absturz ruft er anwenden() für die Zeilen hinter dem Stand der Zustandsdatei erneut.

Die Zustände sind die der Datenbank (Migration 0013, release_anfragen.status), derselbe Wortlaut, damit der Dienst sie
spiegeln kann. Zuordnung zur Arbeit (der Status nennt die Stufe, in der die Anfrage gerade arbeitet; ab "freigegeben" hängt
die Arbeit am nächsten Meilenstein):

  angefragt        Anfrage steht, Quelle wird festgehalten            ->  arbeit (selbst) oder uebernommen (main)
  arbeit           Agent arbeitet in der Arbeitskopie (Stufe 3)       ->  uebernommen
  uebernommen      Übernahme der Quelle als Commit C1                 ->  pruefung1
  pruefung1        Prüfstrecke 1: bauen, testen, scannen              ->  review (selbst) oder versionswahl (main)
  review           Review durch lesende Agenten (Stufe 3)             ->  refactor
  refactor         Refactor als Commit R (Stufe 3)                    ->  pruefung2
  pruefung2        Prüfstrecke 2 auf R                                ->  versionswahl
  versionswahl     Versionsvorschlag, Wahl des Admins, Release-Commit V ->  pruefung3
  pruefung3        Prüfstrecke 3 auf V, Klassifizierung durch den Anker ->  wartet_freigabe
  wartet_freigabe  wartet auf die Freigabe eines Admins (Datenbank)   ->  freigegeben
  freigegeben      Veröffentlichen: Branch, Tag, Release, main        ->  veroeffentlicht
  veroeffentlicht  der Anker stellt bereit                            ->  bereitgestellt
  bereitgestellt   der Anker schaltet um                              ->  umgeschaltet
  umgeschaltet     Bewährung (30 Minuten, der Anker)                  ->  bewaehrt
  bewaehrt, zurueckgenommen, abgebrochen: Ende. Fehler, Zeitüberschreitung und Abbruch führen vor der Bereitstellung nach
  abgebrochen (mit Grund), danach über den Rückweg nach zurueckgenommen.

Quelle main (Mensch, Pull Request) überspringt arbeit, review, refactor und pruefung2 (Konzept D.2: ohne Agent und ohne
Refactor); die Datenbank erlaubt das vor der Freigabe ausdrücklich.
"""

from __future__ import annotations

import re

REIHENFOLGE = ("angefragt", "arbeit", "uebernommen", "pruefung1", "review", "refactor", "pruefung2", "versionswahl",
               "pruefung3", "wartet_freigabe", "freigegeben", "veroeffentlicht", "bereitgestellt", "umgeschaltet",
               "bewaehrt")
ZUSTAENDE = REIHENFOLGE + ("zurueckgenommen", "abgebrochen")
ENDZUSTAENDE = frozenset({"bewaehrt", "zurueckgenommen", "abgebrochen"})
OFFENE = frozenset(ZUSTAENDE) - ENDZUSTAENDE  # genau eine davon darf es geben (Datenbank: release_anfragen_ein_offener_idx)
QUELLEN = ("selbst", "main")
WEG = {
    "main": ("angefragt", "uebernommen", "pruefung1", "versionswahl", "pruefung3", "wartet_freigabe", "freigegeben",
             "veroeffentlicht", "bereitgestellt", "umgeschaltet", "bewaehrt"),
    "selbst": REIHENFOLGE,
}

# Wer einen Übergang bewirken darf. "dienst": das Ergebnis einer Stufe des Release-Dienstes. "anker": der Anker (oder der
# Freigabe-Dienst des Ankers) hat es in der Datenbank gesetzt, der Dienst beobachtet es nur. Der Release-Dienst setzt
# diese Zustände nie selbst in die Datenbank (die Rolle wamo_release darf es nicht, Migration 0013).
VOM_ANKER = frozenset({"freigegeben", "bereitgestellt", "umgeschaltet", "bewaehrt", "zurueckgenommen"})
BELEGE = frozenset({"stufe", "datenbank", "abbruch", "zeit", "fehler", "zurueck", "sperre"})
BELEG_ABBRUCH = frozenset({"abbruch", "zeit", "fehler", "zurueck", "sperre"})

BEOBACHTEN = frozenset({"wartet_freigabe", "umgeschaltet"})  # keine eigene Arbeit, nur auf die Datenbank schauen
ANKER_FOLGE = frozenset({"veroeffentlicht", "bereitgestellt"})  # Arbeit ruft den Anker, danach beobachten
BEREITGESTELLT_AB = REIHENFOLGE.index("bereitgestellt")  # ab hier ist etwas ausgerollt: Ende nur über den Rückweg
# Ab hier kann der Anker schon etwas getan haben (die Arbeit von veroeffentlicht ruft ihn): Jedes Ende der Anfrage verlangt
# zuerst einen gelungenen Rückweg. Die Datenbank kennt von veroeffentlicht aus nur abgebrochen, ab bereitgestellt auch
# zurueckgenommen.
ANKER_BERUEHRT_AB = REIHENFOLGE.index("veroeffentlicht")
GENESIS = "0" * 64


def _tabelle() -> dict[str, frozenset[str]]:
    t: dict[str, set[str]] = {z: set() for z in ZUSTAENDE}
    for weg in WEG.values():
        for a, b in zip(weg, weg[1:]):
            t[a].add(b)
    for z in REIHENFOLGE[:BEREITGESTELLT_AB]:
        t[z].add("abgebrochen")  # wie die Datenbank (von < umgeschaltet), aber nie aus bereitgestellt: dort gilt der Rückweg
    for z in ("bereitgestellt", "umgeschaltet"):
        t[z].add("zurueckgenommen")
    return {z: frozenset(v) for z, v in t.items()}


UEBERGAENGE = _tabelle()

# Zeitgrenzen je Zustand in Sekunden: so lange darf die Anfrage in diesem Zustand bleiben. Danach bricht der Dienst ab
# (vor der Bereitstellung) oder verlangt den Rückweg (ab bereitgestellt). Keine Grenze ist unendlich.
FRIST_S = {
    "angefragt": 900, "arbeit": 3600, "uebernommen": 600, "pruefung1": 2700, "review": 1800, "refactor": 1800,
    "pruefung2": 2700, "versionswahl": 86400, "pruefung3": 900, "wartet_freigabe": 86400, "freigegeben": 900,
    "veroeffentlicht": 1800, "bereitgestellt": 900, "umgeschaltet": 3600,
}
GESAMT_FRIST_S = 3 * 86400  # so lange darf eine Anfrage insgesamt offen sein
VERSUCHE_MAX = 3  # Läufe derselben Stufe (der erste und zwei Wiederaufnahmen nach einem Absturz)
HISTORIE_MAX = 50
ANFRAGEN_JE_TAG = 3  # höchstens so viele Anfragen je Admin und 24 Stunden (Konzept H, Kontingent)
TAG_S = 86400

ENDE_GRUENDE = frozenset({
    "abbruch_person", "zurueck", "zeitlimit", "gesamtzeitlimit", "stufe_fehler", "stufe_ausnahme", "stufe_ungueltig",
    "stufe_nicht_verfuegbar", "pruefung_fehlgeschlagen", "pruefstand_unvollstaendig", "daten_unvollstaendig",
    "klassifizierung_fehlt", "schutzschicht_gesperrt", "zweiter_admin_noetig", "freigabe_abweichung",
    "wiederaufnahme_erschoepft", "gesperrt", "rueckweg_nach_zeitlimit", "verwaist", "abgebrochen_extern",
})

# Pflichtzeilen des Prüfstands je Runde (Konzept D.3 und F). Jede muss "ok" sein, sonst endet die Anfrage. Eine Stufe, die
# eine Zeile weglässt, gilt als fehlgeschlagen: fail closed.
PRUEFSTAND_VOLL = ("bauen", "tests", "typen_lint", "secret_scan", "fremdverweis", "abhaengigkeiten", "migrations_lint",
                   "wirkungsflaechen", "schutzschicht")
PRUEFSTAND_PFLICHT = {1: PRUEFSTAND_VOLL, 2: PRUEFSTAND_VOLL,
                      3: ("bauen", "tests", "secret_scan", "fremdverweis", "version_bytegleich", "schutzschicht")}
STUFE_RUNDE = {"pruefung1": 1, "pruefung2": 2, "pruefung3": 3}

# Schutzschicht (Konzept E.2). Bis Stufe 4 sind Änderungen der Quelle selbst mit Treffer gesperrt.
SCHUTZ_SELBST_FREIGESCHALTET = False

# ---------------------------------------------------------------- Werte aus Stufen: nur diese Schlüssel, nur diese Form
HEX_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
SHA_RE = re.compile(r"[0-9a-f]{64}")
VERSION_RE = re.compile(r"(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})")
GRUND_RE = re.compile(r"[a-z][a-z0-9_]{1,39}")
UUID_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
PRUEFSTAND_NAME_RE = re.compile(r"[a-z][a-z0-9_]{1,23}")
PRUEFSTAND_WERTE = frozenset({"ok", "fehler", "uebersprungen"})
KLASSEN = frozenset({"normal", "sonder", "gesperrt"})
HEX_SCHLUESSEL = frozenset({"basis_commit", "c1_commit", "r_commit", "v_commit", "tree_id", "treffer_sha"})
DATEN_SCHLUESSEL = HEX_SCHLUESSEL | {"bump", "version_vorschlag", "version_final", "pruefstand", "klasse", "treffer_anzahl",
                                     "refactor_verworfen"}
# Diese Schlüssel gibt der Dienst an die Datenbank weiter (Spaltenrechte der Rolle wamo_release in Migration 0013)
DB_SPALTEN = ("basis_commit", "c1_commit", "r_commit", "v_commit", "tree_id", "bump", "version_vorschlag", "version_final",
              "pruefstand")
# Welche Zustände der Dienst selbst in die Datenbank schreiben darf (Rolle wamo_release in der Migration 0013)
DB_SCHREIBBAR = frozenset({"arbeit", "uebernommen", "pruefung1", "review", "refactor", "pruefung2", "versionswahl",
                           "pruefung3", "wartet_freigabe", "veroeffentlicht", "abgebrochen"})


class ZustandDefekt(Exception):
    pass


class UebergangVerboten(Exception):
    def __init__(self, grund: str):
        super().__init__(grund)
        self.grund = grund


def version_tupel(v: str) -> tuple[int, int, int]:
    a, b, c = v.split(".")
    return int(a), int(b), int(c)


def pruefstand_pruefen(obj: object) -> dict[str, str]:
    if not isinstance(obj, dict) or len(obj) > 16:
        raise ValueError("pruefstand")
    for k, v in obj.items():
        if not isinstance(k, str) or not PRUEFSTAND_NAME_RE.fullmatch(k) or not isinstance(v, str) \
                or v not in PRUEFSTAND_WERTE:
            raise ValueError("pruefstand")
    return dict(obj)


def daten_pruefen(daten: object) -> dict:
    """Prüft die Werte, die eine Stufe liefert (und die später im Audit-Protokoll stehen). Wirft ValueError."""
    try:
        return _daten_pruefen(daten)
    except TypeError as e:  # unhashbare Werte (Listen, Objekte) in einem Feld, das Zeichenketten erwartet
        raise ValueError("Wert hat den falschen Typ") from e


def _daten_pruefen(daten: object) -> dict:
    if not isinstance(daten, dict) or len(daten) > len(DATEN_SCHLUESSEL):
        raise ValueError("daten")
    ergebnis: dict = {}
    for k, v in daten.items():
        if not isinstance(k, str) or k not in DATEN_SCHLUESSEL:
            raise ValueError(f"unbekannter Schlüssel {k!r}"[:60])
        if k == "treffer_sha":
            ok = isinstance(v, str) and SHA_RE.fullmatch(v) is not None
        elif k in HEX_SCHLUESSEL:
            ok = isinstance(v, str) and HEX_RE.fullmatch(v) is not None
        elif k == "bump":
            ok = isinstance(v, str) and v in ("patch", "minor", "major")
        elif k in ("version_vorschlag", "version_final"):
            ok = isinstance(v, str) and VERSION_RE.fullmatch(v) is not None
        elif k == "klasse":
            ok = isinstance(v, str) and v in KLASSEN
        elif k == "treffer_anzahl":
            ok = isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 200
        elif k == "refactor_verworfen":
            ok = v is True
        else:  # pruefstand
            ergebnis[k] = pruefstand_pruefen(v)
            continue
        if not ok:
            raise ValueError(f"Wert von {k} ungültig")
        ergebnis[k] = v
    if "version_final" in ergebnis:
        vorschlag = ergebnis.get("version_vorschlag")
        if vorschlag is not None and version_tupel(ergebnis["version_final"]) < version_tupel(vorschlag):
            raise ValueError("Version unter dem Vorschlag")  # Konzept F: erhöhen ja, senken nie
    return ergebnis


# ---------------------------------------------------------------- Übergänge
def naechster(status: str, quelle: str) -> str | None:
    """Der reguläre Nachfolger im Weg der Quelle (None am Ende)."""
    weg = WEG[quelle]
    if status not in weg:
        return None
    i = weg.index(status)
    return weg[i + 1] if i + 1 < len(weg) else None


def uebergang_pruefen(von: str, nach: str, beleg: str, quelle: str) -> None:
    """Wirft UebergangVerboten(grund), wenn der Übergang nicht in der Tabelle steht oder der Beleg nicht passt."""
    if not (isinstance(von, str) and isinstance(nach, str) and isinstance(beleg, str)) \
            or von not in ZUSTAENDE or nach not in ZUSTAENDE or beleg not in BELEGE:
        raise UebergangVerboten("zustand_unbekannt")
    if von in ENDZUSTAENDE:
        raise UebergangVerboten("anfrage_beendet")
    if nach not in UEBERGAENGE[von]:
        raise UebergangVerboten("uebergang_nicht_erlaubt")
    if nach == "abgebrochen":
        if beleg not in BELEG_ABBRUCH:
            raise UebergangVerboten("beleg_passt_nicht")
        return
    if nach == "zurueckgenommen":
        if beleg not in ("zurueck", "datenbank"):
            raise UebergangVerboten("beleg_passt_nicht")
        return
    if naechster(von, quelle) != nach:
        raise UebergangVerboten("uebergang_nicht_erlaubt")  # der Weg der Quelle lässt keine Abkürzung zu
    # Zustände des Ankers entstehen nie aus einer Stufe des Dienstes, die übrigen nie aus der Beobachtung der Datenbank
    if beleg != ("datenbank" if nach in VOM_ANKER else "stufe"):
        raise UebergangVerboten("beleg_passt_nicht")


def frist_ablauf(a: dict) -> float:
    """Zeitpunkt (Sekunden seit 1970), ab dem die Anfrage in ihrem Zustand zu lange ist."""
    return a["seit"] + FRIST_S[a["status"]]


# ---------------------------------------------------------------- Zustandsdatei
def leerer_zustand() -> dict:
    # audit_hash ist der Hash der Protokollzeile audit_n: Er bindet die Datei an die Kette (ein zurückgespielter Stand oder ein
    # rotiertes Protokoll fällt beim Start auf). spiegel_offset ist die Byte-Position hinter der Zeile spiegel_n.
    return {"schema": 1, "audit_n": 0, "audit_hash": GENESIS, "spiegel_n": 0, "spiegel_offset": 0, "anfrage": None,
            "abgeschlossen": []}


ANFRAGE_FELDER = frozenset({"id", "profil", "quelle", "status", "seq", "start", "seit", "versuch", "fertig", "daten",
                            "ende_grund"})
ABGESCHLOSSEN_FELDER = frozenset({"id", "profil", "status", "grund", "start", "ende"})


def _zahl(v: object, maximum: float = 1e12) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= maximum


def _ganz(v: object, maximum: int = 10**9) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= maximum


def zustand_pruefen(obj: object) -> dict:
    """Streng: nur bekannte Felder, nur gültige Werte. Wirft ZustandDefekt. Gibt eine unabhängige Kopie zurück."""
    try:
        return _zustand_pruefen(obj)
    except (TypeError, AttributeError, KeyError) as e:  # JSON-gültig, aber mit vertauschten Typen
        raise ZustandDefekt(f"Zustand hat falsche Typen ({type(e).__name__})") from e


def _zustand_pruefen(obj: object) -> dict:
    if not isinstance(obj, dict) or set(obj) != {"schema", "audit_n", "audit_hash", "spiegel_n", "spiegel_offset", "anfrage",
                                                 "abgeschlossen"} or obj["schema"] != 1 or isinstance(obj["schema"], bool):
        raise ZustandDefekt("Kopf ungültig")
    if not all(_ganz(obj[k]) for k in ("audit_n", "spiegel_n", "spiegel_offset")) \
            or not isinstance(obj["audit_hash"], str) or not SHA_RE.fullmatch(obj["audit_hash"]) \
            or (obj["audit_n"] == 0) != (obj["audit_hash"] == GENESIS):
        raise ZustandDefekt("Zähler ungültig")
    z = leerer_zustand()
    for k in ("audit_n", "audit_hash", "spiegel_n", "spiegel_offset"):
        z[k] = obj[k]
    a = obj["anfrage"]
    if a is not None:
        if not isinstance(a, dict) or set(a) != ANFRAGE_FELDER:
            raise ZustandDefekt("Anfrage hat die falschen Felder")
        if not (isinstance(a["id"], str) and UUID_RE.fullmatch(a["id"]) and isinstance(a["profil"], str)
                and UUID_RE.fullmatch(a["profil"]) and a["quelle"] in QUELLEN and a["status"] in OFFENE
                and _ganz(a["seq"]) and _zahl(a["start"]) and _zahl(a["seit"]) and _ganz(a["versuch"], VERSUCHE_MAX)
                and isinstance(a["fertig"], bool) and a["ende_grund"] is None):
            raise ZustandDefekt("Anfrage hat ungültige Werte")
        if a["status"] not in WEG[a["quelle"]]:
            raise ZustandDefekt("Zustand passt nicht zum Weg der Quelle")
        try:
            a = {**a, "daten": daten_pruefen(a["daten"])}
        except ValueError as e:
            raise ZustandDefekt(f"Daten ungültig: {e}") from e
        z["anfrage"] = a
    liste = obj["abgeschlossen"]
    if not isinstance(liste, list) or len(liste) > HISTORIE_MAX:
        raise ZustandDefekt("Liste der abgeschlossenen Anfragen ungültig")
    for e in liste:
        if not (isinstance(e, dict) and set(e) == ABGESCHLOSSEN_FELDER and isinstance(e["id"], str)
                and UUID_RE.fullmatch(e["id"]) and isinstance(e["profil"], str) and UUID_RE.fullmatch(e["profil"])
                and e["status"] in ENDZUSTAENDE and (e["grund"] is None or e["grund"] in ENDE_GRUENDE)
                and _zahl(e["start"]) and _zahl(e["ende"])):
            raise ZustandDefekt("Eintrag der abgeschlossenen Anfragen ungültig")
        z["abgeschlossen"].append(dict(e))
    return z


# ---------------------------------------------------------------- Zeilen des Write-Ahead-Logs anwenden
WAL_EREIGNISSE = frozenset({"angefordert", "stufe_start", "stufe_ende", "uebergang"})


def anfrage_von(zeile: dict) -> dict:
    """Die neue Anfrage aus der Audit-Zeile 'angefordert' (alles, was nötig ist, steht in der Zeile)."""
    return {"id": zeile["anfrage"], "profil": zeile["profil"], "quelle": zeile["quelle"], "status": "angefragt", "seq": 0,
            "start": zeile["zeit"], "seit": zeile["zeit"], "versuch": 0, "fertig": False, "daten": {}, "ende_grund": None}


def anwenden(z: dict, zeile: dict) -> None:
    """Wendet eine Zeile des Write-Ahead-Logs auf den Zustand z an (verändert z). Wirft ZustandDefekt, wenn die Zeile nicht
    zum Zustand passt. Dieselbe Funktion arbeitet im Betrieb und bei der Wiederaufnahme nach einem Absturz."""
    try:
        _anwenden(z, zeile)
    except (KeyError, TypeError, AttributeError) as e:
        raise ZustandDefekt(f"Zeile passt nicht ({type(e).__name__})") from e


def _anwenden(z: dict, zeile: dict) -> None:
    art = zeile.get("ereignis")
    a = z["anfrage"]
    try:
        if art == "angefordert":
            if a is not None or zeile["quelle"] not in QUELLEN or not UUID_RE.fullmatch(zeile["anfrage"]) \
                    or not UUID_RE.fullmatch(zeile["profil"]) or not _zahl(zeile["zeit"]):
                raise ZustandDefekt("angefordert passt nicht zum Zustand")
            z["anfrage"] = anfrage_von(zeile)
            return
        if a is None or zeile.get("anfrage") != a["id"]:
            raise ZustandDefekt("Zeile gehört nicht zur offenen Anfrage")
        if art == "stufe_start":
            if zeile["status"] != a["status"] or zeile["versuch"] != a["versuch"] + 1 or zeile["versuch"] > VERSUCHE_MAX:
                raise ZustandDefekt("stufe_start passt nicht zum Zustand")
            a["versuch"] = zeile["versuch"]
        elif art == "stufe_ende":
            if zeile["status"] != a["status"]:
                raise ZustandDefekt("stufe_ende passt nicht zum Zustand")
            if zeile.get("ok") is True:
                a["fertig"] = True
        elif art == "uebergang":
            if zeile["von"] != a["status"] or zeile["seq"] != a["seq"] + 1:
                raise ZustandDefekt("uebergang passt nicht zum Zustand")
            try:
                uebergang_pruefen(zeile["von"], zeile["nach"], zeile["beleg"], a["quelle"])
                daten = daten_pruefen(zeile.get("daten", {}))
            except (UebergangVerboten, ValueError) as e:
                raise ZustandDefekt(f"uebergang nicht zulässig: {getattr(e, 'grund', e)}") from e
            a["status"], a["seq"], a["seit"] = zeile["nach"], zeile["seq"], zeile["zeit"]
            a["versuch"], a["fertig"] = 0, False
            a["daten"].update(daten)
            if zeile["nach"] in ENDZUSTAENDE:
                grund = zeile.get("grund")
                if grund is not None and grund not in ENDE_GRUENDE:
                    raise ZustandDefekt("Endgrund unbekannt")
                z["abgeschlossen"].append({"id": a["id"], "profil": a["profil"], "status": zeile["nach"], "grund": grund,
                                           "start": a["start"], "ende": zeile["zeit"]})
                del z["abgeschlossen"][:-HISTORIE_MAX]
                z["anfrage"] = None
        else:
            raise ZustandDefekt("Zeile ist kein Zustandsereignis")
    except KeyError as e:
        raise ZustandDefekt(f"Feld fehlt: {e.args[0]}") from e
