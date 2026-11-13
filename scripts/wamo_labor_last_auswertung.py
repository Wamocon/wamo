"""Auswertung des Lasttests (scripts/wamo_labor_last.py, nur Standardbibliothek): Kennzahlen, Bestehen je Kriterium, Bericht.

Alle Zahlen stammen aus zwei Quellen: den Schritten der Browserinstanzen (erster Ton, Verstummen, falsche Unterbrechungen, Abbrüche) und den lesenden
Messungen am Kern (CPU, Speicher, Aufgaben, Journalzählung). Daraus entstehen je Stufe ein Wörterbuch (JSON-fähig) und die Urteile je Kriterium.

Grundsatz: Eine fehlende Messung ist nie ein Bestehen. Ohne Daten, ohne lesbares Journal oder ohne Angabe zum Ratenlimit lautet das Urteil "offen".
Die Bezugsgrößen, die in den Unterlagen fehlen, stehen im Bericht als Annahme (zum Beispiel: 100 Prozent CPU ist ein Kern).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import wamo_labor_last_plan as pl
from wamo_labor_last_server import JournalZaehlung, Messung

BESTANDEN, DURCHGEFALLEN, OFFEN = "bestanden", "durchgefallen", "offen"
GIB = pl.GIB

# Was die Last nicht belegt und was dieses Werkzeug nicht misst (Bericht und docs/labor-lasttest.md)
NICHT_GEMESSEN = (
    "Verzug der Ereignisschleife und Datenbankverbindungen des Kerns: nicht gemessen (der Kern bietet dafür keinen lesenden Weg, den dieses Werkzeug nutzt).",
    "Tokens je Zug und Tokens je Minute: nur, wenn der Kern Latenzzeilen (LATENZ_TOKEN) ins Journal schreibt, sonst offen. Cache-Treffer und Websuchen: nicht gemessen.",
    "Der Dienst der Kollegen: gemessen wird, was der Kern und der Browser sehen, nicht die Last im Dienst selbst.",
    "Echte Menschen: Die Sprechproben sind eine Windows-Stimme, das Murmeln ist erzeugt oder eine Aufnahme. Das Gerät, das WLAN und echte Mikrofone sind nicht dabei.",
    "Nikos Büro-Betrieb als parallele Last und das Ratenlimit des Schlüssels: nur mit Angabe des Limits und nur mit echter Parallellast aussagekräftig.",
)


@dataclass(frozen=True)
class SchrittErgebnis:
    """Ein Schritt einer Sitzung (eine Sprechprobe, ihre Antwort, bei A1 auch der Einwurf)."""
    sitzung: int
    kennung: str
    karte: str
    beginn_s: float  # Sekunden seit Beginn der Stufe
    erster_ton_ms: float | None  # Ende der Sprechprobe bis zum ersten Ton von WAMO, None: keine Antwort
    antwort_s: float | None = None
    verstummen_ms: float | None = None  # Einwurf bis zum Ende der Wiedergabe
    einwurf: bool = False  # war ein Einwurf geplant
    einwurf_ausgeloest: bool = False  # lief WAMO lange genug, damit der Einwurf gespielt wurde
    verpasst: bool = False  # WAMO verstummte nach dem Einwurf nicht rechtzeitig
    falsche_unterbrechungen: int = 0
    abbruch: str = ""  # Grund, wenn die Sitzung unerwartet endete
    zeitueberschreitung: bool = False
    freigabe_geklickt: bool = False


# ---------- Kennzahlen ----------

def quantil(werte: Sequence[float], p: float) -> float | None:
    """Der Wert am nächsten Rang (aufgerundet): bei wenigen Messungen lieber zu streng als zu nachsichtig."""
    if not werte:
        return None
    geordnet = sorted(werte)
    return geordnet[max(0, min(len(geordnet) - 1, math.ceil(p * len(geordnet)) - 1))]


def kennzahlen(werte: Sequence[float | None]) -> dict:
    vorhanden = [w for w in werte if w is not None]
    return {"n": len(vorhanden), "p50": quantil(vorhanden, 0.5), "p95": quantil(vorhanden, 0.95), "max": max(vorhanden, default=None)}


def cpu_prozente(proben: Sequence[Messung]) -> list[float]:
    """CPU je Intervall zwischen zwei aufeinanderfolgenden Messungen in Prozent eines Kerns (100 ist ein Kern, mehr ist möglich)."""
    aus = []
    for a, b in zip(proben, proben[1:]):
        if a.cpu_usec is None or b.cpu_usec is None:
            continue
        dauer, delta = b.zeit - a.zeit, b.cpu_usec - a.cpu_usec
        if dauer > 0 and delta >= 0:
            aus.append(delta / (dauer * 1e6) * 100)
    return aus


def journal_summe(proben: Sequence[Messung]) -> dict:
    """Die Journalzählung aller Messungen einer Stufe. lesbar: wahr, falsch (mindestens ein Weg scheiterte) oder None (nie gefragt)."""
    mit = [p for p in proben if p.journal_lesbar is not None]
    zaehler = [p.journal for p in mit if isinstance(p.journal, JournalZaehlung)]
    minuten: dict[int, list[int]] = {}
    for j in zaehler:
        for minute, (anfragen, eingabe) in j.minuten.items():
            summe = minuten.setdefault(minute, [0, 0])
            summe[0] += anfragen
            summe[1] += eingabe
    felder = ("r429", "soniox", "anfragen", "unterbrechungen", "pipeline_fehler", "token_zuege", "token_eingabe", "token_cache_lesen",
              "token_cache_schreiben")
    aus = {f: sum(getattr(j, f) for j in zaehler) for f in felder}
    aus["lesbar"] = None if not mit else all(p.journal_lesbar for p in mit)
    aus["abgeschnitten"] = any(j.abgeschnitten for j in zaehler)
    aus["anfragen_je_minute_max"] = max((m[0] for m in minuten.values()), default=0)
    aus["tokens_je_minute_max"] = max((m[1] for m in minuten.values()), default=0)
    return aus


def _max_oder_none(werte: Sequence[int | None]) -> int | None:
    vorhanden = [w for w in werte if w is not None]
    return max(vorhanden) if vorhanden else None


def stufe_auswerten(stufe: pl.Stufe, schritte: Sequence[SchrittErgebnis], proben: Sequence[Messung], browser_429: int, konsolenfehler: int,
                    dauer_s: float = 0.0) -> dict:
    cpu = cpu_prozente(proben)
    karten = sorted({s.karte for s in schritte})
    return {
        "nummer": stufe.nummer, "name": stufe.name, "sitzungen": stufe.sitzungen, "minuten": stufe.minuten, "snr_db": stufe.snr_db, "dauer_s": round(dauer_s),
        "schritte": len(schritte), "ohne_antwort": sum(1 for s in schritte if s.erster_ton_ms is None and not s.abbruch),
        "erster_ton_ms": kennzahlen([s.erster_ton_ms for s in schritte]),
        "erster_ton_je_karte": {k: kennzahlen([s.erster_ton_ms for s in schritte if s.karte == k]) for k in karten},
        "verstummen_ms": kennzahlen([s.verstummen_ms for s in schritte]), "verstummen_verpasst": sum(1 for s in schritte if s.verpasst),
        "einwuerfe": sum(1 for s in schritte if s.einwurf_ausgeloest),
        "falsche_unterbrechungen": sum(s.falsche_unterbrechungen for s in schritte), "abbrueche": sum(1 for s in schritte if s.abbruch),
        "zeitueberschreitungen": sum(1 for s in schritte if s.zeitueberschreitung),
        "cpu_prozent": {"proben": len(cpu), "mittel": sum(cpu) / len(cpu) if cpu else None, "p95": quantil(cpu, 0.95), "max": max(cpu, default=None)},
        "speicher_max": _max_oder_none([p.speicher for p in proben]), "speicher_spitze": _max_oder_none([p.speicher_spitze for p in proben]),
        "aufgaben_max": _max_oder_none([p.aufgaben for p in proben]), "journal": journal_summe(proben),
        "browser_429": browser_429, "konsolenfehler": konsolenfehler,
    }


# ---------- Kriterien ----------

def _k(name: str, status: str, wert: str, grenze: str, text: str = "") -> dict:
    return {"name": name, "status": status, "wert": wert, "grenze": grenze, "text": text}


def _ms(wert: float | None) -> str:
    return "keine Messung" if wert is None else f"{wert:.0f} ms"


def _gib(wert: int | float | None) -> str:
    return "keine Messung" if wert is None else f"{wert / GIB:.2f} GiB".replace(".", ",")


def _reine_stufe(stufen: Sequence[dict], sitzungen: int) -> dict | None:
    return next((s for s in stufen if s["sitzungen"] == sitzungen and s.get("snr_db") is None), None)


def _p95(stufe: dict | None) -> float | None:
    return stufe["erster_ton_ms"]["p95"] if stufe and stufe["erster_ton_ms"]["n"] else None


def _kriterium_erster_ton(stufen: Sequence[dict]) -> list[dict]:
    p1, p8 = _p95(_reine_stufe(stufen, 1)), _p95(_reine_stufe(stufen, 8))
    grenze1 = f"höchstens {pl.P95_ERSTER_TON_MS} ms"
    k1 = (_k("p95 des ersten Tons bei 8 Sitzungen", OFFEN, _ms(p8), grenze1, "Stufe mit 8 Sitzungen ohne Murmeln fehlt oder ohne Antwort")
          if p8 is None else _k("p95 des ersten Tons bei 8 Sitzungen", BESTANDEN if p8 <= pl.P95_ERSTER_TON_MS else DURCHGEFALLEN, _ms(p8), grenze1))
    grenze2 = f"höchstens das {str(pl.FAKTOR_ZU_EINER_SITZUNG).replace('.', ',')}-fache des Werts bei 1 Sitzung"
    if p8 is None or p1 is None:
        return [k1, _k("p95 mit 8 Sitzungen, 1,5-fache von 1 Sitzung", OFFEN, f"{_ms(p8)} zu {_ms(p1)}", grenze2, "braucht beide Stufen mit Antworten")]
    ok = p8 <= pl.FAKTOR_ZU_EINER_SITZUNG * p1
    return [k1, _k("p95 mit 8 Sitzungen, 1,5-fache von 1 Sitzung", BESTANDEN if ok else DURCHGEFALLEN, f"{_ms(p8)} zu {_ms(p1)}", grenze2)]


def _kriterium_abbrueche(stufen: Sequence[dict]) -> dict:
    if not stufen:
        return _k("0 Abbrüche", OFFEN, "keine Messung", "0")
    summe = sum(s["abbrueche"] for s in stufen)
    return _k("0 Abbrüche", BESTANDEN if summe == 0 else DURCHGEFALLEN, str(summe), "0")


def _kriterium_cpu(stufen: Sequence[dict]) -> dict:
    werte = [s["cpu_prozent"]["p95"] for s in stufen if s["cpu_prozent"]["proben"] and s["cpu_prozent"]["p95"] is not None]
    grenze = f"unter {pl.CPU_MAX_PROZENT:.0f} Prozent eines Kerns (p95 der Stichproben je Stufe)"
    if not werte:
        return _k("CPU des Kerns", OFFEN, "keine Messung", grenze, "Bezug (ein Kern oder die ganze Maschine) steht nicht in den Unterlagen, hier ein Kern")
    spitze = max(werte)
    return _k("CPU des Kerns", BESTANDEN if spitze < pl.CPU_MAX_PROZENT else DURCHGEFALLEN, f"{spitze:.1f} Prozent", grenze)


def _kriterium_speicher(stufen: Sequence[dict]) -> dict:
    werte = [s["speicher_max"] for s in stufen if s["speicher_max"] is not None]
    grenze = f"unter {_gib(pl.SPEICHER_MAX_BYTES)}"
    if not werte:
        return _k("Speicher des Kerns", OFFEN, "keine Messung", grenze)
    return _k("Speicher des Kerns", BESTANDEN if max(werte) < pl.SPEICHER_MAX_BYTES else DURCHGEFALLEN, _gib(max(werte)), grenze)


def _kriterium_429(stufen: Sequence[dict]) -> dict:
    journal = sum(s["journal"]["r429"] for s in stufen)
    browser = sum(s["browser_429"] for s in stufen)
    wert = f"{journal} im Journal, {browser} im Browser"
    if journal + browser > 0:
        return _k("Kein 429", DURCHGEFALLEN, wert, "0")
    if not stufen or any(s["journal"]["lesbar"] is not True for s in stufen):
        return _k("Kein 429", OFFEN, wert, "0", "das Journal des Kerns war nicht lesbar oder wurde nicht gefragt, ein unlesbares Journal gilt nie als kein 429")
    return _k("Kein 429", BESTANDEN, wert, "0")


def _kriterium_rate(stufen: Sequence[dict], name: str, feld: str, limit: int | None, einheit: str) -> dict:
    spitze = max((s["journal"][feld] for s in stufen), default=0)
    if limit is None:
        return _k(name, OFFEN, f"Spitze {spitze} {einheit}", "70 Prozent des Ratenlimits", "Limit des Schlüssels nicht angegeben (Konsole, --ratenlimit-...)")
    grenze = f"unter {pl.RATENLIMIT_ANTEIL * limit:.0f} {einheit} (70 Prozent von {limit})"
    if spitze == 0 or any(s["journal"]["lesbar"] is not True for s in stufen):
        return _k(name, OFFEN, f"Spitze {spitze} {einheit}", grenze, "keine Zählung im Journal (nicht lesbar oder keine passenden Zeilen)")
    return _k(name, BESTANDEN if spitze < pl.RATENLIMIT_ANTEIL * limit else DURCHGEFALLEN, f"Spitze {spitze} {einheit}", grenze)


def kriterien_pruefen(stufen: Sequence[dict], abbruchgrund: str, ratenlimit_anfragen: int | None, ratenlimit_tokens: int | None) -> list[dict]:
    """Die Kriterien aus docs/testlabor-wamo.md, 3.7. Murmelstufen zählen nur für Abbrüche, CPU, Speicher und 429, nicht für den ersten Ton."""
    return [*_kriterium_erster_ton(stufen), _kriterium_abbrueche(stufen), _kriterium_cpu(stufen), _kriterium_speicher(stufen),
            _kriterium_429(stufen),
            _kriterium_rate(stufen, "Spitze der Anfragen je Minute", "anfragen_je_minute_max", ratenlimit_anfragen, "Anfragen je Minute"),
            _kriterium_rate(stufen, "Spitze der Tokens je Minute", "tokens_je_minute_max", ratenlimit_tokens, "Tokens je Minute")]


def gesamturteil(kriterien: Sequence[dict], abbruchgrund: str) -> str:
    """Durchgefallen, sobald ein Kriterium durchfällt. Bestanden nur, wenn alle bestehen und der Lauf bis zum Ende kam. Sonst offen."""
    stati = {k["status"] for k in kriterien}
    if DURCHGEFALLEN in stati:
        return DURCHGEFALLEN
    return BESTANDEN if stati == {BESTANDEN} and not abbruchgrund else OFFEN


# ---------- Bericht ----------

def _n(wert: float | None, stellen: int = 0) -> str:
    return "nicht gemessen" if wert is None else f"{wert:.{stellen}f}".replace(".", ",")


def _stufenzeile(s: dict) -> str:
    j = s["journal"]
    return (f"| {s['nummer']} | {s['name']} | {s['schritte']} | {_n(s['erster_ton_ms']['p50'])} | {_n(s['erster_ton_ms']['p95'])} | "
            f"{_n(s['verstummen_ms']['p95'])} | {s['falsche_unterbrechungen']} | {s['abbrueche']} | {_n(s['cpu_prozent']['p95'], 1)} | "
            f"{_gib(s['speicher_max'])} | {j['r429']} und {s['browser_429']} | {j['soniox']} |")


def _abschnitt_stufen(e: dict) -> list[str]:
    if not e["stufen"]:
        return ["## Stufen", "", "Es wurde keine Stufe gemessen.", ""]
    kopf = ["## Stufen", "", "Zeiten in Millisekunden, CPU in Prozent eines Kerns (p95 der 15-Sekunden-Stichproben), 429 als Journal und Browser.", "",
            "| Stufe | Sitzungen | Schritte | erster Ton p50 | erster Ton p95 | Verstummen p95 | falsche Unterbrechungen | Abbrüche | CPU p95 | Speicher max | 429 | Soniox-Fehler |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    zeilen = [_stufenzeile(s) for s in e["stufen"]]
    mehr = ["", "Weitere Zahlen je Stufe: Anfragen je Minute (Spitze), Unterbrechungen im Journal gegen die Einwürfe der Probe, Aufgaben, Zeitüberschreitungen.", "",
            "| Stufe | Anfragen je Minute | Unterbrechungen im Journal | Einwürfe | Verstummen verpasst | Aufgaben max | ohne Antwort | Zeitüberschreitungen |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    mehr += [f"| {s['nummer']} | {s['journal']['anfragen_je_minute_max']} | {s['journal']['unterbrechungen']} | {s['einwuerfe']} | "
             f"{s['verstummen_verpasst']} | {_n(s['aufgaben_max'])} | {s['ohne_antwort']} | {s['zeitueberschreitungen']} |" for s in e["stufen"]]
    return kopf + zeilen + mehr + [""]


def _abschnitt_kriterien(e: dict) -> list[str]:
    zeilen = ["## Bestehen", "", "| Kriterium | Grenze | Wert | Urteil |", "| --- | --- | --- | --- |"]
    zeilen += [f"| {k['name']} | {k['grenze']} | {k['wert']} | {k['status']} |" for k in e["kriterien"]]
    hinweise = [f"- {k['name']}: {k['text']}" for k in e["kriterien"] if k["text"]]
    return zeilen + [""] + hinweise + ([""] if hinweise else [])


def _abschnitt_kosten(e: dict) -> list[str]:
    a, k = e["annahmen"], e["kosten_schaetzung"]
    usd = "nicht geschätzt (Preise nicht angegeben)" if k["usd"] is None else f"{k['usd']:.2f} USD".replace(".", ",")
    return ["## Kosten und Annahmen", "",
            f"Vorab geschätzt: {k['zuege']} Züge, {k['eingabe_tokens']} Eingabe-Tokens, {k['ausgabe_tokens']} Ausgabe-Tokens, {k['anfragen']:.0f} Anfragen, Kosten {usd}.", "",
            "Annahmen (nicht gemessen, nicht geprüft):", "",
            f"- Eingabe-Tokens je Zug: {a['eingabe_tokens_je_zug']} (Büro-Prompt laut docs/gespraech-optimierung.md, der Labor-Prompt ist kürzer)",
            f"- Ausgabe-Tokens je Zug: {a['ausgabe_tokens_je_zug']} (Annahme ohne Beleg in den Unterlagen)",
            f"- Anfragen je Zug: {a['anfragen_je_zug']} (2 bis 3 laut Konzept 3.7)",
            "- Preise: nur mit --preis-eingabe und --preis-ausgabe, das Werkzeug trägt keine Preise ein.", ""]


def _abschnitt_aufraeumen(e: dict) -> list[str]:
    if not e.get("aufraeumen"):
        return ["## Aufräumen", "", "Es wurde nichts aufgeräumt (der Lauf hat nichts verändert).", ""]
    zeilen = ["## Aufräumen", ""]
    for a in e["aufraeumen"]:
        zeilen.append(f"- {'ok' if a['ok'] else 'FEHLGESCHLAGEN'}: {a['schritt']}" + (f" ({a['text']})" if a["text"] else ""))
    if any(not a["ok"] for a in e["aufraeumen"]):
        zeilen += ["", "Ein Schritt ist gescheitert: von Hand nacharbeiten (docs/labor-lasttest.md, Abschnitt Aufräumen von Hand) und die Zählung mit `scripts/wamo_labor_nachlauf.py nachlauf` prüfen."]
    return zeilen + [""]


def bericht_text(e: dict) -> str:
    """Der Bericht (Markdown) zu einem Ergebnis. Er enthält keine Zugangsdaten, keine Gesprächstexte und keine Journalzeilen."""
    urteil = {BESTANDEN: "bestanden", DURCHGEFALLEN: "durchgefallen", OFFEN: "offen, nicht abschließend"}[e["gesamt"]]
    kopf = [f"# Lasttest {e['lauf']}", "", f"Urteil: **{urteil}**", ""]
    if e["abbruchgrund"]:
        kopf += [f"Der Lauf endete vorzeitig: {e['abbruchgrund']}", ""]
    lauf = ["## Lauf", "", "| Angabe | Wert |", "| --- | --- |", f"| Ausführung | {e['ausfuehrung']} |", f"| Beginn | {e['beginn']} |", f"| Ende | {e['ende']} |",
            f"| Schema | {e['schema']} |", f"| Kern-Instanz | wamo-core@{e['slot']} |", f"| Seed der Denkpausen | {e['seed']} |", ""]
    grenzen = ["## Was die Last nicht belegt", ""] + [f"- {t}" for t in NICHT_GEMESSEN] + [""]
    return "\n".join(kopf + lauf + _abschnitt_kriterien(e) + _abschnitt_stufen(e) + _abschnitt_kosten(e) + _abschnitt_aufraeumen(e) + grenzen)
