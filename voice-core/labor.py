"""Politik der Laborprofile (Testlabor 28.11.2026): alle Listen und Prüfungen an einer Stelle, ohne Abhängigkeit von bot.py.

Ein Laborprofil ist ein Profil mit labor_art (platz oder schaufenster), Rolle mitglied und aktiv = false. Wirksam freigeschaltet ist es
nur über labor_aktiv; ältere Kerne kennen die Spalte nicht, sehen aktiv = false und lehnen es mit 403 ab. Jede Einschränkung ist eine
Erlaubnisliste und gilt fail-closed: Was nicht ausdrücklich eingetragen ist, ist gesperrt (neue Routen, neue Werkzeuge, neue
Nachrichtentypen). Durchgesetzt wird im Kern, nicht im Prompt und nicht in der Oberfläche:

  * Gate (auth_gate.py)        labor_aktiv statt aktiv, stimmige Felder (Rolle mitglied, Platz), unbekannte Art heißt platz
  * Pfade (AccessMiddleware)   pfad_pruefen: exakter Regex je Methode, Default deny, keine Normalisierung (Abweichung wird abgelehnt)
  * Nachrichten                rtvi_nachricht_erlaubt (vor dem RTVI-Verarbeiter, labor_rtvi.py) und client_nachricht_erlaubt
                               (on_client_message in bot.py)
  * Werkzeuge                  werkzeuge_pruefen: Kontext und am LLM registrierte Namen müssen in der Erlaubnisliste stehen
  * Entwicklung                entwicklung_arten: nie für platz, für schaufenster nur Recherche
Der Aufrufer reicht nur die Identität aus der Zugangskontrolle herein, nie Werte aus dem Gespräch.
"""

from __future__ import annotations

import inspect
import os
import re
from collections.abc import Iterable

ARTEN = ("platz", "schaufenster")
STRENGSTE_ART = "platz"  # ein unbekannter Wert in labor_art heißt platz, nie "kein Labor"
PLATZ_MIN, PLATZ_MAX = 1, 16
CODE_PFAD_GESPERRT = "labor_pfad_gesperrt"
CODE_ZU_OFT = "labor_zu_viele_anfragen"  # /start und Offer eines Profils über der Grenze (labor_limit.AnfragenFenster), Status 429
GRUND_ZU_OFT = "Zu viele Anfragen in kurzer Zeit. Bitte kurz warten und dann noch einmal versuchen."
CODE_PROFIL = "labor_profil_unstimmig"
GRUND_PROFIL = "Das Laborprofil ist nicht stimmig eingerichtet."
GRUND_DEAKTIVIERT = "Das W.A.I.S.-Profil ist deaktiviert."
RTVI_LABEL = "rtvi-ai"
RTVI_DURCHLASS = frozenset({"client-ready", "send-text"})  # Standardnachrichten des Browsers, alle anderen fallen weg
MAX_TEXT_ZEICHEN = 4000  # Länge einer getippten Eingabe (send-text); mehr ist kein Gespräch, sondern Last auf dem geteilten Schlüssel

# Werkzeuge einer Laborsitzung. Das Gedächtnis (merken), PC, Bildschirm, Fernzugriff, Entwicklung, Boss, Geräte, geteilte Arbeit und
# ausgabe_nachladen gehören nicht dazu und werden nie gebaut. Die Ticket- und Mail-Werkzeuge hängt der Kern nur über die Labor-Freigabe ein.
WERKZEUGE_BASIS = frozenset({"aktuelle_zeit", "websuche", "canvas_zeigen"})
WERKZEUGE_TICKET = frozenset({"ticket_suchen", "ticket_anlegen", "ticket_aendern", "ticket_verschieben", "ticket_kommentieren",
                              "mail_senden"})
_WERKZEUGE = {"platz": WERKZEUGE_BASIS | WERKZEUGE_TICKET, "schaufenster": WERKZEUGE_BASIS | WERKZEUGE_TICKET}

# Entwicklungsaufträge: nie für einen Platz, im Schaufenster nur die Recherche (der Kern-Kanal setzt zusätzlich ARTEN=recherche)
_ENTWICKLUNG = {"platz": frozenset(), "schaufenster": frozenset({"recherche"})}

# Client-Nachrichten der Oberfläche (RTVI client-message): Typ und Job. Alles andere wird verworfen.
_CLIENT_NACHRICHTEN = {"platz": frozenset({("freigabe", "labor")}), "schaufenster": frozenset({("freigabe", "labor")})}

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_LABOR_API = re.compile(r"/wamo/api/labor/[a-z][a-z0-9_]{0,31}")
# (Methode, Pfad, Art). Exakt, ohne Normalisierung: die Pipecat-Pfade /start, /api/offer und /sessions/<id>/api/offer, dazu die Laborrouten.
_ROUTEN_ALLE = (
    ("POST", re.compile(r"/start"), "start"),
    ("POST", re.compile(r"/api/offer"), "offer"),
    ("PATCH", re.compile(r"/api/offer"), "ice"),
    ("POST", re.compile(rf"/sessions/{_UUID}/api/offer"), "offer"),
    ("PATCH", re.compile(rf"/sessions/{_UUID}/api/offer"), "ice"),
    ("GET", _LABOR_API, "api"),
    ("POST", _LABOR_API, "api"),
)
_ROUTEN_ART = {
    "platz": _ROUTEN_ALLE,
    "schaufenster": _ROUTEN_ALLE + (("GET", re.compile(r"/wamo/api/vorgaenge"), "api"),),
}


class LaborPolitikFehler(RuntimeError):
    """Eine Laborsitzung verstößt gegen ihre Erlaubnisliste (zum Beispiel ein Werkzeug außerhalb der Liste): sie wird nicht gebaut."""


class LaborAbgelehnt(Exception):
    """Die Laborsitzung wird nicht angenommen oder endet (Durchlauf, Einwilligung, Dienst). status und code für die Oberfläche."""

    def __init__(self, grund: str, status: int = 403, code: str | None = None):
        super().__init__(grund)
        self.grund, self.status, self.code = grund, status, code


# ---------- Art und Profil ----------
def art_normalisieren(roh: object) -> str | None:
    """None heißt kein Laborprofil. Jeder andere Wert, auch ein leerer oder unbekannter, ergibt eine bekannte Art: der strengste Fall platz."""
    if roh is None:
        return None
    if isinstance(roh, str) and roh in ARTEN:
        return roh
    return STRENGSTE_ART


def ist_labor(identity: object) -> bool:
    """Gehört diese geprüfte Identität zu einem Laborprofil? Die gemeinsame Schnittstelle für alle Stränge."""
    return art_normalisieren(getattr(identity, "labor_art", None)) is not None


def art_von(profil: object) -> str | None:
    return art_normalisieren(getattr(profil, "labor_art", None))


def _platz_ok(platz: object) -> bool:
    return isinstance(platz, int) and not isinstance(platz, bool) and PLATZ_MIN <= platz <= PLATZ_MAX


def laborfelder_stimmig(art: str | None, rolle: object, platz: object, aktiv: object = True) -> bool:
    """Die Regeln der Migration, im Kern noch einmal geprüft: Rolle mitglied, platz verlangt einen Platz von 1 bis 16, schaufenster keinen.
    aktiv ist der Wert der Spalte aktiv (bei einem Laborprofil dauerhaft falsch)."""
    if art is None:
        return True
    if rolle != "mitglied" or aktiv is True:
        return False
    if art == "platz":
        return _platz_ok(platz)
    return platz is None


def profil_problem(profil: object) -> str | None:
    """Kurzer Grund für das Log, wenn ein Laborprofil nicht den Regeln entspricht (dann gilt es als nicht zugelassen), sonst None."""
    art = art_von(profil)
    if art is None:
        return None
    if getattr(profil, "rolle", None) != "mitglied":
        return "rolle"
    if getattr(profil, "aktiv", None) is not False:
        return "aktiv"
    if not laborfelder_stimmig(art, "mitglied", getattr(profil, "labor_platz", None), False):
        return "platz"
    return None


def profil_aktiv(profil: object) -> bool:
    """Ist das Profil wirksam freigeschaltet? Ein Laborprofil nur über labor_aktiv (und stimmige Felder), alle anderen über aktiv."""
    if art_von(profil) is None:
        return bool(getattr(profil, "aktiv", False))
    return getattr(profil, "labor_aktiv", None) is True and profil_problem(profil) is None


def aenderung(identity: object, profil: object) -> str | None:
    """Grund, warum eine laufende Sitzung enden muss, weil sich Art, Freischaltung oder Platz des Profils geändert haben, sonst None."""
    alt, neu = art_normalisieren(getattr(identity, "labor_art", None)), art_von(profil)
    if alt != neu:
        return "Die Art des Profils hat sich geändert."
    if alt is None:
        return None
    if not profil_aktiv(profil):
        return "Das Laborprofil ist nicht mehr freigeschaltet."
    if getattr(profil, "labor_platz", None) != getattr(identity, "labor_platz", None):
        return "Der Platz des Profils hat sich geändert."
    return None


# ---------- HTTP-Pfade ----------
def pfad_pruefen(art: object, methode: object, pfad: object, raw_path: object = None) -> str | None:
    """Art des Pfads ("start", "offer", "ice", "api"), wenn ein Laborprofil ihn mit dieser Methode aufrufen darf, sonst None.
    Exakter Vergleich: kein Zusammenfassen von //, keine Punktsegmente, keine Prozentcodierung, keine Großschreibung, kein Schrägstrich am
    Ende. raw_path (ASGI, wie empfangen): enthält er etwas anderes als den Pfad selbst, ist die Anfrage gesperrt."""
    if not isinstance(methode, str) or not isinstance(pfad, str):
        return None
    if raw_path is not None:
        if not isinstance(raw_path, (bytes, bytearray)):
            return None
        try:
            roh = bytes(raw_path).decode("ascii").split("?", 1)[0]
        except UnicodeDecodeError:
            return None
        if roh != pfad:
            return None
    for erlaubte_methode, muster, kind in _ROUTEN_ART[art_normalisieren(art) or STRENGSTE_ART]:
        if methode == erlaubte_methode and muster.fullmatch(pfad):
            return kind
    return None


# ---------- Client-Nachrichten ----------
def client_nachricht_erlaubt(art: object, typ: object, data: object) -> bool:
    """Schicht a (on_client_message): erlaubt ist nur die Freigabe mit job labor."""
    if not isinstance(typ, str) or not isinstance(data, dict):
        return False
    job = data.get("job")
    return isinstance(job, str) and (typ, job) in _CLIENT_NACHRICHTEN[art_normalisieren(art) or STRENGSTE_ART]


def rtvi_nachricht_erlaubt(art: object, message: object) -> bool:
    """Schicht b (vor dem RTVI-Verarbeiter): client-ready, send-text (mit Längengrenze) und client-message mit erlaubtem Inhalt. Jeder
    andere Typ, auch jeder künftige, fällt weg."""
    if not isinstance(message, dict) or message.get("label") != RTVI_LABEL:
        return False
    typ = message.get("type")
    if not isinstance(typ, str):
        return False
    if typ == "send-text":
        data = message.get("data")
        inhalt = data.get("content") if isinstance(data, dict) else None
        return isinstance(inhalt, str) and len(inhalt) <= MAX_TEXT_ZEICHEN
    if typ in RTVI_DURCHLASS:
        return True
    if typ == "client-message":
        data = message.get("data")
        return isinstance(data, dict) and client_nachricht_erlaubt(art, data.get("t"), data.get("d"))
    return False


# ---------- Werkzeuge, Entwicklung, Zugang ----------
def werkzeuge_erlaubt(art: object) -> frozenset[str]:
    return _WERKZEUGE[art_normalisieren(art) or STRENGSTE_ART]


def werkzeuge_pruefen(identity: object, namen: Iterable[object], *, genau: frozenset[str] | None = None) -> None:
    """Die Namen der Werkzeuge einer Laborsitzung (im Kontext und am LLM registriert) müssen die Basiswerkzeuge enthalten und dürfen nichts
    außerhalb der Erlaubnisliste enthalten. genau: die Liste, die diese Sitzung tatsächlich haben muss (Basis, mit Platz dazu Ticket und Mail): dann
    muss die Menge ihr gleichen, nichts Fremdes und nichts Fehlendes. Sonst wirft sie LaborPolitikFehler, und die Sitzung wird nicht gebaut."""
    art = art_normalisieren(getattr(identity, "labor_art", None))
    if art is None:
        raise LaborPolitikFehler("werkzeuge_pruefen ohne Laborprofil")
    vorhanden = set(namen)
    fremd = sorted(str(n) for n in vorhanden - _WERKZEUGE[art])
    if fremd:
        raise LaborPolitikFehler(f"Werkzeuge außerhalb der Laborliste: {', '.join(fremd)}")
    fehlend = sorted(WERKZEUGE_BASIS - vorhanden)
    if fehlend:
        raise LaborPolitikFehler(f"Basiswerkzeuge fehlen in der Laborsitzung: {', '.join(fehlend)}")
    if genau is not None:
        if not genau <= _WERKZEUGE[art]:
            raise LaborPolitikFehler("Die erwartete Werkzeugliste liegt außerhalb der Laborliste")
        zu_viel, zu_wenig = sorted(str(n) for n in vorhanden - genau), sorted(genau - vorhanden)
        if zu_viel or zu_wenig:
            raise LaborPolitikFehler(
                "Die Werkzeuge der Laborsitzung weichen von der erwarteten Liste ab"
                + (f", zu viel: {', '.join(zu_viel)}" if zu_viel else "") + (f", fehlt: {', '.join(zu_wenig)}" if zu_wenig else ""))


def registrierte_namen(llm: object) -> set[object]:
    """Alle am LLM registrierten Funktionsnamen (auch der Sammelhandler None). Ist die Registratur nicht lesbar, wirft sie: fail-closed."""
    funktionen = getattr(llm, "_functions", None)
    if not isinstance(funktionen, dict):
        raise LaborPolitikFehler("Die am LLM registrierten Werkzeuge sind nicht lesbar")
    return set(funktionen)


def entwicklung_arten(identity: object) -> frozenset[str]:
    """Auftragsarten für Entwicklungsaufträge dieser Laborsitzung: platz keine, schaufenster nur recherche."""
    art = art_normalisieren(getattr(identity, "labor_art", None))
    if art is None:
        raise ValueError("entwicklung_arten gilt nur für Laborprofile")
    return _ENTWICKLUNG[art]


def zugang_parameter(identity: object) -> dict[str, str]:
    """Zusatzargument für modellzugang.zugang_laden: labor_art, nur bei Laborprofilen und nur aus der Identität. Solange zugang_laden das
    Argument nicht kennt (Strang S liefert es), bleibt es leer, und das Laborprofil bekommt wie jedes Profil ohne Schlüssel die Hausmodelle."""
    art = art_normalisieren(getattr(identity, "labor_art", None))
    if art is None:
        return {}
    from modellzugang import zugang_laden

    return {"labor_art": art} if "labor_art" in inspect.signature(zugang_laden).parameters else {}


# ---------- Umgebung ----------
def text_speichern_an() -> bool:
    """WAMO_LABOR_TEXT_SPEICHERN: nur der Wert "an" schaltet das Speichern des Gesprächstexts ein (Standard aus, Variante A)."""
    return (os.getenv("WAMO_LABOR_TEXT_SPEICHERN") or "").strip().lower() == "an"


def einwilligung_min() -> int | None:
    """WAMO_LABOR_EINWILLIGUNG_MIN: kleinste gültige Versionsnummer des Hinweises (ganze Zahl ab 1). Fehlt sie oder ist sie ungültig, nimmt
    der Kern keine Laborsitzung an (fail-closed)."""
    roh = (os.getenv("WAMO_LABOR_EINWILLIGUNG_MIN") or "").strip()
    if not re.fullmatch(r"[0-9]{1,6}", roh):
        return None
    wert = int(roh)
    return wert if wert >= 1 else None
