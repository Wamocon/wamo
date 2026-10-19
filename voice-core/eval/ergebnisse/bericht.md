WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn

# WAMO: Abnahme der Gesprächsmodelle

Erzeugt von `voice-core/eval/abnahme.py`. Testsatz `c58a2acd2aef`, Prompt `b1cc5f84ed02`, Bewertung `e2dc88aeb460`. Freigabe für das Gespräch nur bei mindestens 95 % richtigen Werkzeugaufrufen, mindestens 98 % korrekten Markern, erstem Token p95 höchstens 0,60 s, allen Muss-Fällen und ohne Infrastrukturfehler, und das in 3 Läufen hintereinander (p95 über alle zusammen). Format und Inhalt sind Zusatzangaben ohne Schwelle. Gemessen wird vom Rechner, auf dem der Lauf startet (Feld stand.messort in der JSON-Datei); der Testsatz deckt nur die Konfiguration Admin, PC-Steuerung an, Entwicklungsaufträge gesperrt ab. Die Marker-Regel der Persona wurde an diesem Testsatz abgestimmt, die Claude-Zahlen sind deshalb eher zu günstig.

## Stand je Modell (letzter gültiger Lauf)

| Anbieter | Modell | Läufe | Werkzeug | mit / ohne Aufruf | Marker | stumm / verspätet | Format | Inhalt | Muss | erstes Token p50 / p95 | erster Text p95 | nach Werkzeug p95 | Tokens/s | Kosten (kalt) | Freigabe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude | claude-haiku-5-5 | 1 | 96 % | 28/30 / 20/20 | 92 % | 3 / 1 | 98 % | 83 % | ok | 0,78 s / 1,33 s | 1,30 s | 1,25 s | 265 | 0,0755 USD | offen (1 von 3 Läufen) |

## Gründe und Auffälligkeiten

- claude claude-haiku-5-5: offen (1 von 3 Läufen).
  - Marker 46/50 statt mindestens 49 (98 %)
  - erstes Token p95 1,33 s statt höchstens 0,60 s

## Alle Läufe

| Datei | Modell | Werkzeug | Marker | Format | p95 | Kosten | Testsatz | Prompt | Aktuell | Notiz |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `2026-10-19_1636_claude_claude-haiku-5-5.json` | claude-haiku-5-5 | 96 % | 74 % | 98 % | 1,37 s | 0,0364 USD | 639b2566c6ed | – | veraltet | Ausgangsstand: Persona ohne eigene Marker-Regel |
| `2026-10-19_1640_claude_claude-haiku-5-5.json` | claude-haiku-5-5 | 92 % | 90 % | 96 % | 1,72 s | 0,0597 USD | 639b2566c6ed | – | veraltet | Persona mit Marker-Regel, Fassung A |
| `2026-10-19_1725_claude_claude-haiku-5-5.json` | claude-haiku-5-5 | 86 % | 80 % | 98 % | 1,34 s | 0,0592 USD | 475dcb4bfd6c | – | veraltet | Fassung B (zusätzlicher Satz zu ○), verworfen |
| `2026-10-19_1727_claude_claude-haiku-5-5.json` | claude-haiku-5-5 | 96 % | 90 % | 98 % | 1,42 s | 0,0602 USD | 475dcb4bfd6c | 2a36014d7234 | veraltet | Fassung C (nur ● positiv formuliert), übernommen |
| `2026-10-19_1730_claude_claude-haiku-5-5.json` | claude-haiku-5-5 | 98 % | 90 % | 100 % | 1,32 s | 0,0112 USD | 475dcb4bfd6c | 2a36014d7234 | veraltet | Fassung C, Wiederholung |
| `2026-10-19_181007_claude_claude-haiku-5-5.json` | claude-haiku-5-5 | 88 % | 92 % | 96 % | 1,33 s | 0,0513 USD | 3b6de5b2ae32 | b1cc5f84ed02 | veraltet | Referenz nach Review: Testsatz mit Muss-Fällen, Persona Fassung C |
| `2026-10-19_181317_claude_claude-haiku-5-5.json` | claude-haiku-5-5 | 96 % | 92 % | 98 % | 1,33 s | 0,0108 USD | c58a2acd2aef | b1cc5f84ed02 | ja | Referenz, endgültiger Testsatz, Persona Fassung C |

---

WAMOCON GmbH · Seite 1
