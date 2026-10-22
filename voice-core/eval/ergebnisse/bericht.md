WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn

# WAMO: Abnahme der Gesprächsmodelle

Erzeugt von `voice-core/eval/abnahme.py`. Testsatz `78bbdc4a4f8c`, Prompt `9c67aba6e1ae`, Bewertung `e3c9fbbf660c`. Freigabe für das Gespräch nur bei mindestens 95 % richtigen Werkzeugaufrufen, mindestens 98 % korrekten Markern, erstem Token p95 höchstens 0,60 s (nur Hausmodelle; Claude ist Referenz und Rückfall, seine Zeiten stehen nur zum Vergleich da), allen Muss-Fällen und ohne Infrastrukturfehler, und das in 3 Läufen hintereinander (p95 über alle zusammen). Format und Inhalt sind Zusatzangaben ohne Schwelle. Gemessen wird vom Rechner, auf dem der Lauf startet (Feld stand.messort in der JSON-Datei); der Testsatz deckt nur die Konfiguration Admin, PC-Steuerung an, Entwicklungsaufträge gesperrt ab. Die Marker-Regel der Persona wurde an diesem Testsatz abgestimmt, die Claude-Zahlen sind deshalb eher zu günstig.

## Stand je Modell (letzter gültiger Lauf)

| Anbieter | Modell | Läufe | Werkzeug | mit / ohne Aufruf | Marker | stumm / verspätet | Format | Inhalt | Muss | erstes Token p50 / p95 | erster Text p95 | nach Werkzeug p95 | Tokens/s | Kosten (kalt) | Freigabe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude | claude-haiku-5-5 | 1 | 96 % | 29/31 / 20/20 | 88 % | 3 / 3 | 98 % | 83 % | ok | 0,72 s / 1,04 s | 1,11 s | 1,19 s | 249 | 0,0784 USD | offen (1 von 3 Läufen) |
| litellm | gemma4-31b | 3 | 94 % | 28/31 / 20/20 | 96 % | 1 / 1 | 100 % | 92 % | ok | 0,63 s / 2,99 s | 0,82 s | 2,79 s | 10 | – | nein |
| litellm | qwen3.6-35b | 3 | 78 % | 23/31 / 17/20 | 69 % | 2 / 9 | 94 % | 79 % | ok | 2,69 s / 2,73 s | 2,74 s | 2,81 s | 74 | – | nein |
| litellm | qwen3.8-27b | 3 | 76 % | 20/31 / 19/20 | 53 % | 2 / 29 | 96 % | 88 % | ok | 1,41 s / 1,59 s | 1,57 s | 1,28 s | 24 | – | nein |
| litellm | sokrates-fast | 3 | 88 % | 26/31 / 19/20 | 96 % | 2 / 0 | 100 % | 88 % | n02 | 0,64 s / 3,07 s | 0,81 s | 2,00 s | 9 | – | nein |
| litellm | wamo-allgemein | 1 | 82 % | 22/31 / 20/20 | 51 % | 3 / 23 | 98 % | 79 % | v03 | 1,44 s / 1,52 s | 1,64 s | 2,15 s | 25 | – | offen (1 von 3 Läufen) |

## Gründe und Auffälligkeiten

- claude claude-haiku-5-5: offen (1 von 3 Läufen).
  - Marker 45/51 statt mindestens 50 (98 %)
- litellm gemma4-31b: nein.
  - 2026-10-20_1636: Werkzeug 45/51 statt mindestens 49 (95 %)
  - 2026-10-20_1636: erstes Token p95 3,04 s statt höchstens 0,60 s
  - 2026-10-20_1643: Werkzeug 47/51 statt mindestens 49 (95 %)
  - 2026-10-20_1643: Marker 48/51 statt mindestens 50 (98 %)
  - 2026-10-20_1643: erstes Token p95 3,00 s statt höchstens 0,60 s
  - 2026-10-20_1649: Werkzeug 48/51 statt mindestens 49 (95 %)
  - 2026-10-20_1649: Marker 49/51 statt mindestens 50 (98 %)
  - 2026-10-20_1649: erstes Token p95 2,99 s statt höchstens 0,60 s
  - erstes Token p95 über 3 Läufe 4,31 s statt höchstens 0,60 s
  - Wechselnde Ergebnisse zwischen den Läufen: c01, c03, m03, v03, v04
- litellm qwen3.6-35b: nein.
  - 2026-10-20_1655: Werkzeug 33/51 statt mindestens 49 (95 %)
  - 2026-10-20_1655: Marker 36/51 statt mindestens 50 (98 %)
  - 2026-10-20_1655: erstes Token p95 2,72 s statt höchstens 0,60 s
  - 2026-10-20_1701: Werkzeug 40/51 statt mindestens 49 (95 %)
  - 2026-10-20_1701: Marker 41/51 statt mindestens 50 (98 %)
  - 2026-10-20_1701: erstes Token p95 2,73 s statt höchstens 0,60 s
  - 2026-10-20_1706: Werkzeug 40/51 statt mindestens 49 (95 %)
  - 2026-10-20_1706: Marker 35/51 statt mindestens 50 (98 %)
  - 2026-10-20_1706: erstes Token p95 2,73 s statt höchstens 0,60 s
  - erstes Token p95 über 3 Läufe 2,73 s statt höchstens 0,60 s
  - Wechselnde Ergebnisse zwischen den Läufen: c01, c02, c03, g04, m01, m03, m04, n02, n03, n04, n05, p01, p02, p03, p04, p05, p07, p08, p09, p10, r03, u01, v01, v02, w02, w03, w04
- litellm qwen3.8-27b: nein.
  - 2026-10-21_0733: Werkzeug 43/51 statt mindestens 49 (95 %)
  - 2026-10-21_0733: Marker 29/51 statt mindestens 50 (98 %)
  - 2026-10-21_0733: erstes Token p95 1,54 s statt höchstens 0,60 s
  - 2026-10-21_0738: Werkzeug 41/51 statt mindestens 49 (95 %)
  - 2026-10-21_0738: Marker 31/51 statt mindestens 50 (98 %)
  - 2026-10-21_0738: erstes Token p95 1,51 s statt höchstens 0,60 s
  - 2026-10-21_0743: Werkzeug 39/51 statt mindestens 49 (95 %)
  - 2026-10-21_0743: Marker 27/51 statt mindestens 50 (98 %)
  - 2026-10-21_0743: erstes Token p95 1,59 s statt höchstens 0,60 s
  - erstes Token p95 über 3 Läufe 1,53 s statt höchstens 0,60 s
  - Wechselnde Ergebnisse zwischen den Läufen: c01, c02, g02, g03, g04, m01, m02, m03, n01, n02, n03, n04, p01, p04, p05, p06, p07, p09, p10, p12, r01, r03, u03, v02, v04, w01, w02, w03, w04, z01, z04
- litellm sokrates-fast: nein.
  - 2026-10-20_1615: Werkzeug 46/51 statt mindestens 49 (95 %)
  - 2026-10-20_1615: erstes Token p95 3,41 s statt höchstens 0,60 s
  - 2026-10-20_1623: Werkzeug 46/51 statt mindestens 49 (95 %)
  - 2026-10-20_1623: Marker 47/51 statt mindestens 50 (98 %)
  - 2026-10-20_1623: erstes Token p95 3,15 s statt höchstens 0,60 s
  - 2026-10-20_1629: Werkzeug 45/51 statt mindestens 49 (95 %)
  - 2026-10-20_1629: Marker 49/51 statt mindestens 50 (98 %)
  - 2026-10-20_1629: erstes Token p95 3,07 s statt höchstens 0,60 s
  - 2026-10-20_1629: Muss-Fälle nicht bestanden: n02
  - erstes Token p95 über 3 Läufe 4,28 s statt höchstens 0,60 s
  - Wechselnde Ergebnisse zwischen den Läufen: c01, c03, i04, m03, n02, p08, v03, v04, w01
- litellm wamo-allgemein: offen (1 von 3 Läufen).
  - Werkzeug 42/51 statt mindestens 49 (95 %)
  - Marker 26/51 statt mindestens 50 (98 %)
  - erstes Token p95 1,52 s statt höchstens 0,60 s
  - Muss-Fälle nicht bestanden: v03

## Alle Läufe

| Datei | Modell | Werkzeug | Marker | Format | p95 | Kosten | Testsatz | Prompt | Aktuell | Notiz |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `2026-10-19_1636_claude_claude-haiku-5-5.json` | claude-haiku-5-5 | 96 % | 74 % | 98 % | 1,37 s | 0,0364 USD | 639b2566c6ed | – | veraltet | Ausgangsstand: Persona ohne eigene Marker-Regel |
| `2026-10-19_1640_claude_claude-haiku-5-5.json` | claude-haiku-5-5 | 92 % | 90 % | 96 % | 1,72 s | 0,0597 USD | 639b2566c6ed | – | veraltet | Persona mit Marker-Regel, Fassung A |
| `2026-10-19_1725_claude_claude-haiku-5-5.json` | claude-haiku-5-5 | 86 % | 80 % | 98 % | 1,34 s | 0,0592 USD | 475dcb4bfd6c | – | veraltet | Fassung B (zusätzlicher Satz zu ○), verworfen |
| `2026-10-19_1727_claude_claude-haiku-5-5.json` | claude-haiku-5-5 | 96 % | 90 % | 98 % | 1,42 s | 0,0602 USD | 475dcb4bfd6c | 2a36014d7234 | veraltet | Fassung C (nur ● positiv formuliert), übernommen |
| `2026-10-19_1730_claude_claude-haiku-5-5.json` | claude-haiku-5-5 | 98 % | 90 % | 100 % | 1,32 s | 0,0112 USD | 475dcb4bfd6c | 2a36014d7234 | veraltet | Fassung C, Wiederholung |
| `2026-10-19_181007_claude_claude-haiku-5-5.json` | claude-haiku-5-5 | 88 % | 92 % | 96 % | 1,33 s | 0,0513 USD | 3b6de5b2ae32 | b1cc5f84ed02 | veraltet | Referenz nach Review: Testsatz mit Muss-Fällen, Persona Fassung C |
| `2026-10-19_181317_claude_claude-haiku-5-5.json` | claude-haiku-5-5 | 96 % | 92 % | 98 % | 1,33 s | 0,0108 USD | c58a2acd2aef | b1cc5f84ed02 | veraltet | Referenz, endgültiger Testsatz, Persona Fassung C |
| `2026-10-20_161517_litellm_sokrates-fast.json` | sokrates-fast | 90 % | 98 % | 98 % | 3,41 s | – | 78bbdc4a4f8c | 9c67aba6e1ae | ja | Hausmodell, think=false |
| `2026-10-20_162308_litellm_sokrates-fast.json` | sokrates-fast | 90 % | 92 % | 100 % | 3,15 s | – | 78bbdc4a4f8c | 9c67aba6e1ae | ja | Hausmodell, think=false |
| `2026-10-20_162949_litellm_sokrates-fast.json` | sokrates-fast | 88 % | 96 % | 100 % | 3,07 s | – | 78bbdc4a4f8c | 9c67aba6e1ae | ja | Hausmodell, think=false |
| `2026-10-20_163647_litellm_gemma4-31b.json` | gemma4-31b | 88 % | 100 % | 100 % | 3,04 s | – | 78bbdc4a4f8c | 9c67aba6e1ae | ja | Hausmodell, think=false |
| `2026-10-20_164324_litellm_gemma4-31b.json` | gemma4-31b | 92 % | 94 % | 100 % | 3,00 s | – | 78bbdc4a4f8c | 9c67aba6e1ae | ja | Hausmodell, think=false |
| `2026-10-20_164958_litellm_gemma4-31b.json` | gemma4-31b | 94 % | 96 % | 100 % | 2,99 s | – | 78bbdc4a4f8c | 9c67aba6e1ae | ja | Hausmodell, think=false |
| `2026-10-20_165549_litellm_qwen3.6-35b.json` | qwen3.6-35b | 65 % | 71 % | 88 % | 2,72 s | – | 78bbdc4a4f8c | 9c67aba6e1ae | ja | Hausmodell, think=false |
| `2026-10-20_170100_litellm_qwen3.6-35b.json` | qwen3.6-35b | 78 % | 80 % | 98 % | 2,73 s | – | 78bbdc4a4f8c | 9c67aba6e1ae | ja | Hausmodell, think=false |
| `2026-10-20_170641_litellm_qwen3.6-35b.json` | qwen3.6-35b | 78 % | 69 % | 94 % | 2,73 s | – | 78bbdc4a4f8c | 9c67aba6e1ae | ja | Hausmodell, think=false |
| `2026-10-21_073356_litellm_qwen3.8-27b.json` | qwen3.8-27b | 84 % | 57 % | 96 % | 1,54 s | – | 78bbdc4a4f8c | 9c67aba6e1ae | ja | Hausmodell, think=false |
| `2026-10-21_073826_litellm_qwen3.8-27b.json` | qwen3.8-27b | 80 % | 61 % | 100 % | 1,51 s | – | 78bbdc4a4f8c | 9c67aba6e1ae | ja | Hausmodell, think=false |
| `2026-10-21_074308_litellm_qwen3.8-27b.json` | qwen3.8-27b | 76 % | 53 % | 96 % | 1,59 s | – | 78bbdc4a4f8c | 9c67aba6e1ae | ja | Hausmodell, think=false |
| `2026-10-21_074542_claude_claude-haiku-5-5.json` | claude-haiku-5-5 | 96 % | 88 % | 98 % | 1,04 s | 0,0633 USD | 78bbdc4a4f8c | 9c67aba6e1ae | ja | Referenz auf aktuellem Testsatz (51 Züge) |
| `2026-10-22_064337_litellm_wamo-allgemein.json` | wamo-allgemein | 82 % | 51 % | 98 % | 1,52 s | – | 78bbdc4a4f8c | 9c67aba6e1ae | ja | Hausmodell, think=false |

---

WAMOCON GmbH · Seite 1
