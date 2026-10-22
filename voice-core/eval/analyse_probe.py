"""Probe für lange Texte und Analyse (Modellkarte, Fähigkeit "analyse").

Ein erzeugter Projektbericht von 24 Abschnitten (rund 8 000 Token) mit Fakten an verschiedenen Stellen, Ablenkungen
(ähnliche Zahlen aus einem Vorgängerprojekt) und zwei Korrekturen, die spätere Abschnitte an früheren vornehmen.
Sechs Fragen prüfen: Finden, Zusammenrechnen, Korrektur beachten, Zählen über den ganzen Text, Terminverschiebung und
eine Datumsrechnung. Der Bericht entsteht ohne Zufall aus festen Bausteinen, jeder Lauf sieht denselben Text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

START = date(2026, 3, 3)
GO_LIVE = date(2026, 10, 12)
TAGE_BIS_GO_LIVE = (GO_LIVE - START).days  # 223

FUELLER = (
    "Die wöchentliche Abstimmung findet weiterhin am Dienstagvormittag statt und dauert in der Regel eine halbe Stunde.",
    "Das Protokoll der letzten Sitzung liegt im gemeinsamen Ordner und wurde von allen Beteiligten gelesen.",
    "Die Lagerhalle Nord wurde für den Pilotbetrieb mit zusätzlichen Regalbeschriftungen ausgestattet.",
    "Aus Sicht der Fachabteilung ist die Bedienung der Handscanner inzwischen verständlich, einzelne Menüs bleiben zu tief verschachtelt.",
    "Der Lenkungskreis hat die Berichtsform bestätigt und bittet darum, Abweichungen frühzeitig zu melden.",
    "Die Anbindung der Versandetiketten wurde mit dem Dienstleister besprochen und für die kommende Phase vorgemerkt.",
    "Im Test zeigten sich einzelne Verzögerungen bei der Anzeige langer Artikellisten, die Ursache wird untersucht.",
    "Die Schulungsunterlagen wurden überarbeitet und enthalten nun zusätzliche Beispiele aus dem Tagesgeschäft.",
    "Der Datenschutzbeauftragte hat die Protokollierung der Scanvorgänge geprüft und keine Einwände erhoben.",
    "Zwischen Einkauf und Lager besteht Einigkeit darüber, dass Lieferavis künftig automatisch angelegt werden sollen.",
    "Die Oberfläche wurde auf Wunsch der Schichtleiter um eine Ansicht für offene Aufträge ergänzt.",
    "Für die Nachtschicht wurden zwei Ansprechpersonen benannt, die bei Fragen zum Portal erreichbar sind.",
    "Die Qualitätssicherung hat die Testfälle für die Retourenbearbeitung ergänzt und mit den Anwendern abgestimmt.",
    "Das Konzept für die Berechtigungen wurde im Team vorgestellt und wird in der nächsten Woche finalisiert.",
    "Es gab keine Sicherheitsvorfälle im Berichtszeitraum, die regelmäßige Prüfung der Zugänge lief planmäßig.",
    "Die Rückmeldungen der Pilotnutzer wurden gesammelt, nach Dringlichkeit sortiert und im Backlog eingeordnet.",
    "Der Zeitplan für die Einführung in den übrigen Standorten wird erst nach Abschluss des Pilotbetriebs festgelegt.",
    "Zur Entlastung der Fachabteilung übernimmt das Projektteam die Pflege der Stammdaten bis auf Weiteres.",
    "Die Dokumentation der Schnittstellen wurde um Fehlercodes und typische Ursachen erweitert.",
    "Ein Workshop mit den Lagerleitern lieferte wertvolle Hinweise zur Reihenfolge der Arbeitsschritte.",
    "Die Auswertung der Durchlaufzeiten wird monatlich erstellt und im Lenkungskreis vorgestellt.",
    "Für die Stabilität im Dauerbetrieb wurde ein Lasttest mit erhöhter Nutzerzahl durchgeführt und ausgewertet.",
)

ABLENKUNG = {
    3: "Zum Vergleich: Das Vorgängerprojekt Südlicht hatte ein Budget von 190.000 Euro und ein Team von 5 Personen.",
    7: "Beim Vorgängerprojekt Südlicht war der Pilotbetrieb bereits am 14. Mai abgeschlossen.",
    11: "Das Vorgängerprojekt Südlicht kannte nur zwei Risiken, beide mit der Einstufung kritisch.",
    20: "Der Produktivstart des Vorgängerprojekts Südlicht fand am 6. Oktober 2025 statt.",
}

FAKTEN = {
    2: ("Die Projektleitung liegt bei Frau Kerstin Albrecht. Projektstart war der 3. März 2026. "
        "Das ursprüngliche Budget beträgt 240.000 Euro."),
    5: ("Risikoliste, Stand Mai: R1 Schnittstelle zum Warenwirtschaftssystem, Einstufung kritisch. "
        "R2 Schulungsaufwand, Einstufung mittel. R3 Datenmigration, Einstufung kritisch. "
        "R4 Lieferverzug bei den Scannern, Einstufung niedrig."),
    9: "Nachtrag zum Budget: Mit dem Änderungsantrag vom 12. Juni wurde das Budget um 36.000 Euro erhöht.",
    12: ("Risikoliste, Stand Juli: R5 Abhängigkeit vom Hosting-Anbieter, Einstufung kritisch. "
         "R6 Personalausfall im Testteam, Einstufung mittel."),
    14: "Das Projektteam besteht aus 7 Personen, davon 2 externe Mitarbeitende.",
    16: ("Terminübersicht: M1 Fachkonzept am 15. April abgeschlossen. M2 Pilotbetrieb, ursprünglich für den "
         "30. Juni geplant, wurde auf den 21. Juli verschoben. M3 Abnahme am 30. September."),
    17: ("Korrektur zu Abschnitt 14: Nach der Nachbesetzung besteht das Projektteam aus 8 Personen, davon "
         "3 externe Mitarbeitende."),
    19: ("Risikoliste, Stand September: R7 Akzeptanz bei den Lagermitarbeitenden, Einstufung kritisch. "
         "R8 Lizenzkosten der Scannersoftware, Einstufung kritisch. R9 Dokumentation, Einstufung niedrig."),
    23: "Der Produktivstart (Go-live) ist für Montag, den 12. Oktober 2026 vorgesehen.",
}
TITEL = ("Ausgangslage", "Organisation", "Vorgehen", "Anforderungen", "Risiken", "Architektur", "Schnittstellen",
         "Testkonzept", "Budget", "Schulung", "Datenmigration", "Risikobewertung", "Betrieb", "Team",
         "Qualität", "Termine", "Änderungen", "Kommunikation", "Risiken im Herbst", "Dokumentation",
         "Abnahme", "Rollout", "Ausblick", "Schluss")


def bericht() -> str:
    """Der Projektbericht (immer derselbe Text)."""
    teile = ["Projektbericht Lagerportal Nordwind", ""]
    for nr in range(1, 25):
        absaetze = []
        for k in range(9):
            absaetze.append(FUELLER[(nr * 7 + k * 5) % len(FUELLER)])
            if k == 2 and nr in FAKTEN:
                absaetze.append(FAKTEN[nr])
            if k == 5 and nr in ABLENKUNG:
                absaetze.append(ABLENKUNG[nr])
        teile += [f"Abschnitt {nr}: {TITEL[nr - 1]}", " ".join(absaetze), ""]
    return "\n".join(teile)


@dataclass(frozen=True)
class Frage:
    nr: int
    text: str
    muss: tuple[str, ...]  # Muster, die in der Antwort vorkommen müssen (Groß- und Kleinschreibung egal)
    nicht: tuple[str, ...] = ()  # Muster, die nicht vorkommen dürfen


FRAGEN: tuple[Frage, ...] = (
    Frage(1, "Wer leitet das Projekt?", (r"albrecht",)),
    Frage(2, "Wie hoch ist das Budget nach dem Nachtrag insgesamt (in Euro)?", (r"276[. ]?000",)),
    Frage(3, "Wie viele Personen hat das Projektteam laut der letzten Angabe, und wie viele davon sind extern?",
          (r"\b8\b", r"\b3\b")),
    Frage(4, "Wie viele Risiken des Lagerportals sind insgesamt als kritisch eingestuft (nur die Risikolisten "
             "dieses Projekts, nicht das Vorgängerprojekt)?", (r"\b(5|fünf)\b",), (r"\b(4|vier|6|sechs|7|sieben)\b\s*(kritisch|risiken)",)),
    Frage(5, "Wann ist der Meilenstein M2 (Pilotbetrieb) tatsächlich fällig?", (r"21\.\s*(juli|7\.?)|21\.07",)),
    Frage(6, "Wie viele Tage liegen zwischen dem Projektstart und dem Go-live?", (r"\b223\b",)),
)
SYSTEM = ("Du beantwortest Fragen zu einem Bericht. Nutze nur den Bericht. Antworte mit genau einer kurzen Zeile "
          "je Frage im Format 'Nr: Antwort', ohne Erklärung.")


def auftrag() -> str:
    fragen = "\n".join(f"{f.nr}. {f.text}" for f in FRAGEN)
    return f"Bericht:\n\n{bericht()}\n\nFragen:\n{fragen}"


def antworten_zerlegen(text: str) -> dict[int, str]:
    """Zeilen 'Nr: Antwort' (auch '1. ...' oder '1) ...') zu {Nr: Antwort}."""
    ergebnis: dict[int, str] = {}
    for zeile in (text or "").splitlines():
        m = re.match(r"^\W*(\d)\s*[.:)\-]\s*(.+)$", zeile.strip())
        if m and int(m.group(1)) not in ergebnis:
            ergebnis[int(m.group(1))] = m.group(2).strip()
    return ergebnis


def bewerten(text: str) -> tuple[int, list[str]]:
    """(richtig, Gründe der falschen Antworten)."""
    antworten = antworten_zerlegen(text)
    richtig, gruende = 0, []
    for f in FRAGEN:
        a = antworten.get(f.nr, "")
        if not a:
            gruende.append(f"Frage {f.nr}: keine Antwort")
        elif not all(re.search(m, a, re.IGNORECASE) for m in f.muss):
            gruende.append(f"Frage {f.nr}: falsch ({a[:50]})")
        elif any(re.search(m, a, re.IGNORECASE) for m in f.nicht):
            gruende.append(f"Frage {f.nr}: falsche Zahl ({a[:50]})")
        else:
            richtig += 1
    return richtig, gruende
