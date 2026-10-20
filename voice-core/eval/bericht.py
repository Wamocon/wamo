"""Bericht über alle gespeicherten Läufe des Testsatzes (eval/ergebnisse/bericht.md).

Ergebnisdateien werden nur angehängt, nie umgeschrieben. Der Bericht rechnet die Freigabe jedes Mal neu:
  * Ein Lauf zählt nur, wenn Testsatz, Prompt (Persona und Werkzeuge) und Bewertung denselben Hash haben
    wie jetzt; ältere Läufe stehen als "veraltet" in der Liste.
  * Freigabe erst nach drei gültigen Läufen hintereinander, die jeder für sich bestehen (pass^3), mit dem
    p95 des ersten Tokens über alle drei Läufe zusammen. Läufe von einem nicht committeten Stand ergeben
    höchstens eine vorläufige Freigabe.
"""

from __future__ import annotations

import json
from pathlib import Path

import bewertung as bw


def _hashes(d: dict) -> dict:
    """Hashes eines Laufs; ältere Dateien hatten nur testsatz.hash und prompt_hash."""
    h = dict(d.get("hashes") or {})
    h.setdefault("testsatz", (d.get("testsatz") or {}).get("hash"))
    h.setdefault("prompt", d.get("prompt_hash"))
    h.setdefault("bewertung", None)
    return h


def aktuell(d: dict, jetzt: dict) -> bool:
    h = _hashes(d)
    return all(h.get(k) == jetzt.get(k) for k in ("testsatz", "prompt", "bewertung"))


def gueltig(d: dict) -> bool:
    k = d["kennzahlen"]
    return not d.get("abbruch") and k.get("zuege") == k.get("soll", d.get("testsatz", {}).get("zuege"))


def laeufe_laden(ordner: Path) -> list[dict]:
    laeufe = []
    for pfad in sorted(ordner.glob("*.json")):
        daten = json.loads(pfad.read_text(encoding="utf-8"))
        laeufe.append(daten | {"_datei": pfad.name})
    return laeufe


def gruppen_freigabe(laeufe: list[dict]) -> tuple[str, list[str], list[dict]]:
    """(Status, Gründe, gewertete Läufe) für die gültigen Läufe eines Modells mit aktuellem Stand."""
    gewertet = [d for d in laeufe if gueltig(d)][-bw.FREIGABE_LAEUFE:]
    if len(gewertet) < bw.FREIGABE_LAEUFE:
        return f"offen ({len(gewertet)} von {bw.FREIGABE_LAEUFE} Läufen)", [], gewertet
    gruende: list[str] = []
    for d in gewertet:
        ok, g = bw.lauf_freigabe(d["kennzahlen"])
        if not ok:
            gruende += [f"{d['_datei'][:15]}: {x}" for x in g]
    ttft = [z["ttft_s"] if z.get("ttft_s") is not None else bw.ZUG_TIMEOUT_SECS for d in gewertet for z in d["zuege"]]
    p95 = bw.perzentil(ttft, 0.95)
    if p95 is None or p95 > bw.FREIGABE_TTFT_P95:
        gruende.append(f"erstes Token p95 über {len(gewertet)} Läufe {_s(p95)} statt höchstens "
                       f"{_s(bw.FREIGABE_TTFT_P95)}")
    if gruende:
        return "nein", gruende, gewertet
    if all((d.get("stand") or {}).get("git_sauber") for d in gewertet):
        return "ja", [], gewertet
    return "ja, vorläufig (Stand nicht committet)", [], gewertet


def flackernd(gewertet: list[dict]) -> list[str]:
    """Züge, die in einem Lauf bestehen und in einem anderen nicht."""
    if len(gewertet) < 2:
        return []
    ergebnisse: dict[str, set[tuple[bool, bool]]] = {}
    for d in gewertet:
        for z in d["zuege"]:
            ergebnisse.setdefault(z["id"], set()).add((z["werkzeug_ok"], z["marker_ok"]))
    return sorted(i for i, e in ergebnisse.items() if len(e) > 1)


def _p(x: float | None) -> str:
    return "–" if x is None else f"{x * 100:.0f} %"


def _s(x: float | None) -> str:
    return "–" if x is None else f"{x:.2f} s".replace(".", ",")


def _usd(x: float | None) -> str:
    return "–" if x is None else f"{x:.4f} USD".replace(".", ",")


def _md(text: object) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def bericht_schreiben(ordner: Path, jetzt: dict) -> Path:
    laeufe = laeufe_laden(ordner)
    gruppen: dict[tuple[str, str], list[dict]] = {}
    for d in laeufe:
        if aktuell(d, jetzt):
            gruppen.setdefault((d["anbieter"], d["modell"]), []).append(d)
    zeilen = [
        "WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn",
        "",
        "# WAMO: Abnahme der Gesprächsmodelle",
        "",
        f"Erzeugt von `voice-core/eval/abnahme.py`. Testsatz `{jetzt['testsatz']}`, Prompt `{jetzt['prompt']}`, "
        f"Bewertung `{jetzt['bewertung']}`. Freigabe für das Gespräch nur bei mindestens "
        f"{_p(bw.FREIGABE_WERKZEUG)} richtigen Werkzeugaufrufen, mindestens {_p(bw.FREIGABE_MARKER)} korrekten "
        f"Markern, erstem Token p95 höchstens {_s(bw.FREIGABE_TTFT_P95)}, allen Muss-Fällen und ohne "
        f"Infrastrukturfehler, und das in {bw.FREIGABE_LAEUFE} Läufen hintereinander (p95 über alle zusammen). "
        "Format und Inhalt sind Zusatzangaben ohne Schwelle. Gemessen wird vom Rechner, auf dem der Lauf startet "
        "(Feld stand.messort in der JSON-Datei); der Testsatz deckt nur die Konfiguration Admin, PC-Steuerung an, "
        "Entwicklungsaufträge gesperrt ab. Die Marker-Regel der Persona wurde an diesem Testsatz abgestimmt, die "
        "Claude-Zahlen sind deshalb eher zu günstig.",
        "",
        "## Stand je Modell (letzter gültiger Lauf)",
        "",
        "| Anbieter | Modell | Läufe | Werkzeug | mit / ohne Aufruf | Marker | stumm / verspätet | Format | Inhalt "
        "| Muss | erstes Token p50 / p95 | erster Text p95 | nach Werkzeug p95 | Tokens/s | Kosten (kalt) | Freigabe |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    details: list[str] = []
    for (anbieter, modell), gruppe in sorted(gruppen.items()):
        status, gruende, gewertet = gruppen_freigabe(gruppe)
        d = ([x for x in gruppe if gueltig(x)] or gruppe)[-1]
        k = d["kennzahlen"]
        sw = k.get("marker_schwere", {})
        muss = "ok" if not k.get("muss_fehler") else ", ".join(k["muss_fehler"])
        tps = "–" if k.get("tokens_pro_s") is None else f"{k['tokens_pro_s']:.0f}"
        zeilen.append(
            f"| {anbieter} | {_md(modell)} | {len(gewertet)} | {_p(k['werkzeug_quote'])} | "
            f"{k['werkzeug_mit_aufruf']} / {k['werkzeug_ohne_aufruf']} | {_p(k['marker_quote'])} | "
            f"{sw.get('stumm', 0)} / {sw.get('verspaetet', 0)} | {_p(k['format_quote'])} | {_p(k['inhalt_quote'])} | "
            f"{muss} | {_s(k['ttft_p50_s'])} / {_s(k['ttft_p95_s'])} | {_s(k.get('ttfat_p95_s'))} | "
            f"{_s(k.get('ttft_folge_p95_s'))} | {tps} | {_usd(k.get('kosten_kalt_usd'))} | {status} |"
        )
        details.append(f"- {anbieter} {modell}: {status}.")
        details += [f"  - {g}" for g in (gruende or bw.lauf_freigabe(k)[1])]
        if flacker := flackernd(gewertet):
            details.append(f"  - Wechselnde Ergebnisse zwischen den Läufen: {', '.join(flacker)}")
    if not gruppen:
        zeilen.append("| – | – | 0 | – | – | – | – | – | – | – | – | – | – | – | – | – |")
    zeilen += ["", "## Gründe und Auffälligkeiten", "", *(details or ["- Noch kein Lauf mit aktuellem Stand."])]
    zeilen += [
        "", "## Alle Läufe", "",
        "| Datei | Modell | Werkzeug | Marker | Format | p95 | Kosten | Testsatz | Prompt | Aktuell | Notiz |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for d in laeufe:
        k, h = d["kennzahlen"], _hashes(d)
        stand = "ja" if aktuell(d, jetzt) else "veraltet"
        if d.get("abbruch"):
            stand += f", abgebrochen: {_md(d['abbruch'])}"
        zeilen.append(
            f"| `{d['_datei']}` | {_md(d['modell'])} | {_p(k['werkzeug_quote'])} | {_p(k['marker_quote'])} | "
            f"{_p(k['format_quote'])} | {_s(k['ttft_p95_s'])} | {_usd(k.get('kosten_usd'))} | "
            f"{h['testsatz'] or '–'} | {h['prompt'] or '–'} | {stand} | {_md(d.get('notiz') or '–')} |"
        )
    zeilen += ["", "---", "", "WAMOCON GmbH · Seite 1", ""]
    pfad = ordner / "bericht.md"
    pfad.write_text("\n".join(zeilen), encoding="utf-8")
    return pfad
