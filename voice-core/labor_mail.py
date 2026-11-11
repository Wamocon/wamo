"""mail_senden im Testlabor: eine reine Bestätigung "gesendet", keine echte Mail (Entscheidung Q2, Plan docs/testlabor-wamo.md 3.5).

Es gibt in diesem Modul keinen Versandweg, kein Netz und keinen Aufruf an den Dienst: Nach dem Klick auf der Freigabekarte prüft der Kern die
gespeicherten Werte noch einmal und liefert die feste Bestätigung. Der Beweis, dass ohne Klick nichts "rausgeht", ist die Karte und die Reihenfolge
in actions: vor dem Klick gibt es nur pending, nie done, und keine Bestätigung.

Der Domänenfilter steht im Code und gilt vor der Karte und noch einmal beim Klick: genau ein Empfänger, nur ASCII, nur Kleinbuchstaben, Domäne
GLEICH der erlaubten (WAMO_LABOR_MAIL_DOMAIN, Standard labor.test, eine reservierte Domäne, die nie aufgelöst wird), keine Unterdomäne, kein Name
mit Klammern, keine Trennzeichen, keine Steuer- oder Unicode-Zeichen. Der Vergleich ist re.fullmatch auf der Adresse, wie sie ist: nichts wird vorher
zurechtgeschnitten. Ist die Einstellung selbst unbrauchbar, gibt es keine Mail (fail-closed).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from pipecat.adapters.schemas.function_schema import FunctionSchema

from labor_freigaben import Ausgang
from labor_vertrag import MAIL_BETREFF_MAX, MAIL_BETREFF_MIN, MAIL_TEXT_MAX, MAIL_TEXT_MIN, normiere

DOMAIN_STANDARD = "labor.test"
LOKALTEIL = r"[a-z0-9]+(?:[._+-][a-z0-9]+)*"
_LOKALTEIL_MAX = 64
_LABEL = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_DOMAIN_RE = re.compile(rf"{_LABEL}(?:\.{_LABEL})+")
_VORLAUF = 4

F_NICHT_EINGERICHTET = "Das Senden von Mails ist nicht eingerichtet."
F_ADRESSE = ("Die Adresse ist nicht erlaubt. Erlaubt ist genau eine Adresse der Form name@{domain}, komplett klein geschrieben, ohne Namen, "
             "ohne Klammern und ohne weitere Empfänger.")
F_BETREFF = f"Der Betreff braucht {MAIL_BETREFF_MIN} bis {MAIL_BETREFF_MAX} Zeichen."
F_TEXT = f"Der Mail-Text braucht {MAIL_TEXT_MIN} bis {MAIL_TEXT_MAX} Zeichen."
F_ARGUMENTE = "Die Angaben der Mail sind unvollständig."


def erlaubte_domain() -> str | None:
    """Die erlaubte Domäne aus WAMO_LABOR_MAIL_DOMAIN (nicht gesetzt oder leer: labor.test). Eine unbrauchbare Einstellung ergibt None: keine Mail."""
    roh = os.getenv("WAMO_LABOR_MAIL_DOMAIN")
    if roh is None or roh == "":
        return DOMAIN_STANDARD
    if len(roh) > 253 or not roh.isascii() or _DOMAIN_RE.fullmatch(roh) is None or any(label.startswith("xn--") for label in roh.split(".")):
        return None
    return roh


def adresse_erlaubt(an: object, domain: str | None) -> bool:
    """Genau eine Adresse name@<domain>: Kleinbuchstaben, Ziffern, Punkt, Unterstrich, Plus, Minus im Namen, keine Unterdomäne, kein Zusatz."""
    if domain is None or not isinstance(an, str) or len(an) > _LOKALTEIL_MAX + 1 + len(domain) or not an.isascii():
        return False
    return re.fullmatch(rf"{LOKALTEIL}@{re.escape(domain)}", an) is not None and len(an.partition("@")[0]) <= _LOKALTEIL_MAX


def _feld(args: Mapping, name: str, zeilen: bool, lo: int, hi: int) -> str | None:
    wert = args.get(name)
    if not isinstance(wert, str) or len(wert) > hi * _VORLAUF:
        return None
    text = normiere(wert, zeilen=zeilen)
    return text if text is not None and lo <= len(text) <= hi else None


def pruefe_mail(args: object, domain: str | None) -> tuple[dict | None, str | None]:
    """mail_senden: an, betreff, text. Gibt (feste Werte, None) oder (None, Satz). Cc, Bcc und Anhänge gibt es nicht."""
    if domain is None:
        return None, F_NICHT_EINGERICHTET
    if not isinstance(args, Mapping):
        return None, F_ARGUMENTE
    an = args.get("an")
    if not adresse_erlaubt(an, domain):
        return None, F_ADRESSE.format(domain=domain)
    betreff = _feld(args, "betreff", False, MAIL_BETREFF_MIN, MAIL_BETREFF_MAX)
    if betreff is None:
        return None, F_BETREFF
    text = _feld(args, "text", True, MAIL_TEXT_MIN, MAIL_TEXT_MAX)
    if text is None:
        return None, F_TEXT
    return {"an": an, "betreff": betreff, "text": text}, None


def bestaetigen(werte: Mapping[str, str], domain: str | None) -> Ausgang:
    """Die Bestätigung nach dem Klick. Prüft die gespeicherten Werte noch einmal (Adresse, Längen, schon normiert); es geht nichts nach außen."""
    geprueft, _ = pruefe_mail(dict(werte), domain)
    if geprueft is None or set(werte) != {"an", "betreff", "text"} or geprueft != dict(werte):
        return Ausgang(False, "Die Mail wurde nicht bestätigt, die Angaben stimmen nicht mehr. Es ist nichts nach außen gegangen.",
                       code="eingabe_ungueltig")
    return Ausgang(True, f"Mail an {geprueft['an']} gesendet, es ist nichts nach außen gegangen.")


def mail_schema() -> FunctionSchema:
    return FunctionSchema(
        name="mail_senden",
        description=("Sendet eine Mail an genau eine Adresse der Übungsdomäne. Es ist nur eine Bestätigung: Es geht nichts nach außen. "
                     "Das Werkzeug zeigt nur eine Freigabekarte, und der Teilnehmer entscheidet per Klick. Sag danach nur, dass die Karte wartet, "
                     "nie dass die Mail schon gesendet ist. Den Entwurf zeigst du vorher mit canvas_zeigen."),
        properties={
            "an": {"type": "string", "description": "Genau eine Adresse, komplett klein geschrieben, nur aus dem Kontaktverzeichnis der Übung"},
            "betreff": {"type": "string", "description": f"Betreff, {MAIL_BETREFF_MIN} bis {MAIL_BETREFF_MAX} Zeichen, eine Zeile"},
            "text": {"type": "string", "description": f"Mail-Text, {MAIL_TEXT_MIN} bis {MAIL_TEXT_MAX} Zeichen"},
        },
        required=["an", "betreff", "text"],
    )
