WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn

# WAMO: Prüfung der Bausteine Lernen, Zusammenfassung und Vision

Erzeugt von `voice-core/eval/bausteine.py`. Gewertet wird die Qualität; die Zeit ist nur angegeben.

| Datei | Anbieter | Modell | Lernen | Zusammenfassung | Vision | Zeit Lernen / Zusammenf. / Vision |
| --- | --- | --- | --- | --- | --- | --- |
| `2026-10-20_133742_claude_claude-haiku-5-5.json` | claude | claude-haiku-5-5 | 2/4 | 1/1 | 1/1 | 8,3 s / 2,3 s / 7,5 s |
| `2026-10-20_133819_claude_claude-haiku-5-5.json` | claude | claude-haiku-5-5 | 4/4 | – | – | 6,5 s / – / – |
| `2026-10-21_080604_litellm_gemma4-31b.json` | litellm | gemma4-31b | 4/4 | 0/1 | 0/1 | 47,3 s / 20,0 s / 16,4 s |
| `2026-10-21_080739_litellm_sokrates-fast.json` | litellm | sokrates-fast | 4/4 | 0/1 | – | 52,1 s / 20,0 s / – |
| `2026-10-21_080903_claude_claude-haiku-5-5.json` | claude | claude-haiku-5-5 | 3/4 | 1/1 | 1/1 | 7,5 s / 6,7 s / 3,6 s |
| `2026-10-21_081343_litellm_gemma4-31b.json` | litellm | gemma4-31b | – | 1/1 | – | – / 29,9 s / – |
| `2026-10-21_081521_litellm_sokrates-fast.json` | litellm | sokrates-fast | – | 1/1 | – | – / 38,4 s / – |
| `2026-10-21_081655_litellm_wamo-vision.json` | litellm | wamo-vision | – | – | 0/1 | – / – / 14,8 s |
| `2026-10-21_082133_litellm_gemma4-31b.json` | litellm | gemma4-31b | – | – | 1/1 | – / – / 13,9 s |
| `2026-10-21_082206_litellm_wamo-vision.json` | litellm | wamo-vision | – | – | 1/1 | – / – / 13,5 s |
| `2026-10-21_082228_claude_claude-haiku-5-5.json` | claude | claude-haiku-5-5 | – | – | 1/1 | – / – / 5,5 s |

Gründe je Lauf:

- claude-haiku-5-5, lernen fakten: fehlt: tee
- claude-haiku-5-5, lernen injektion: fehlt: 18
- gemma4-31b, zusammenfassung gespraech_lang: Fehler: APITimeoutError: Request timed out.
- gemma4-31b, vision testbild: Senden-Knopf an falscher Stelle (875, 872)
- sokrates-fast, zusammenfassung gespraech_lang: Fehler: APITimeoutError: Request timed out.
- claude-haiku-5-5, lernen geheim_und_andere: fehlt: hamburg
- wamo-vision, vision testbild: Senden-Knopf an falscher Stelle (875, 8730241546793415)

---

WAMOCON GmbH · Seite 1
