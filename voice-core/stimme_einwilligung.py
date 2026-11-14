"""Einwilligung in den Stimmabdruck (DSGVO Art. 9 Abs. 2 lit. a): Text und Version stehen hier, die Oberfläche zeigt sie nur an.

Der Kern schickt den Text beim Start der Sitzung an die Oberfläche (einwilligung_nachricht). Das Einlernen beginnt nur mit der Client-Nachricht
{"aktion": "start", "einwilligung": "stimme-1"} (genau diese Version, einwilligung_gilt). Gespeichert werden Zeitpunkt und Version
(speakers.consent_at, speakers.consent_text_version, siehe stimme_speicher.py).

Wer den Text ändert, vergibt eine neue Version (stimme-2, ...): Ein Abdruck mit älterer Version bleibt gespeichert, die Einwilligung für ein
neues Einlernen gilt aber nur für die aktuelle. Derselbe Wortlaut steht im Abschnitt 4 (Stimmabdruck) von docs/labor-datenschutz-entwurf.md;
test_stimme_einwilligung_doku.py prüft, dass beide übereinstimmen.
"""

from __future__ import annotations

EINWILLIGUNG_VERSION = "stimme-1"
EINWILLIGUNG_TEXT = (
    "Ich willige ausdrücklich ein, dass WAMO aus einer kurzen Aufnahme meiner Stimme einen Stimmabdruck berechnet und in meinem Profil "
    "speichert. Der Stimmabdruck besteht nur aus 192 Zahlen, es wird keine Aufnahme gespeichert. Die Berechnung läuft im Kern von WAMO "
    "auf dem eigenen Server der WAMOCON GmbH. WAMO nutzt den Stimmabdruck allein, um meine Stimme von anderen Stimmen zu unterscheiden: "
    "Stimmen, die WAMO sicher als fremd erkennt, werden nicht verarbeitet und lösen nichts aus. Der Stimmabdruck ist ein biometrisches "
    "Datum nach Artikel 9 DSGVO, deshalb brauche ich diese ausdrückliche Einwilligung. Sie ist freiwillig: Ohne sie nutze ich WAMO ohne "
    "Nachteil weiter. Ich kann den Stimmabdruck jederzeit in den Einstellungen löschen. Das gilt zugleich als Widerruf meiner "
    "Einwilligung, er wirkt für die Zukunft."
)


def einwilligung_nachricht() -> dict:
    """Nachricht an die Oberfläche beim Start der Sitzung."""
    return {"type": "stimmabdruck_einwilligung", "version": EINWILLIGUNG_VERSION, "text": EINWILLIGUNG_TEXT}


def einwilligung_gilt(version: object) -> bool:
    """Nur genau die aktuelle Version zählt (kein Vergleich ohne Typ: True oder 1 sind keine Einwilligung)."""
    return isinstance(version, str) and version == EINWILLIGUNG_VERSION
