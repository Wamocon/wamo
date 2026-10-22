WAMOCON GmbH, Mergenthalerallee 79-81, 65760 Eschborn

# WAMO: Messungen für die Modellkarte

Erzeugt von `voice-core/eval/modellkarte_messen.py`. Hausmodelle über LiteLLM, Claude nur als kleine Referenz (Haiku). Alle Anfragen nacheinander mit Pausen.

## Code (8 Aufgaben, geprüft durch Ausführen)

| Datei | Modell | bestanden | Zeit gesamt | Fehler |
| --- | --- | --- | --- | --- |
| `2026-10-22_055452_code_litellm_wamo-coder.json` | wamo-coder | 7/8 | 75,3 s | intervalle: AssertionError |
| `2026-10-22_055638_code_litellm_qwen3-coder-next-raw.json` | qwen3-coder-next-raw | 7/8 | 71,8 s | lru: Syntaxfehler (Zeile 25) |
| `2026-10-22_055802_code_litellm_qwen3-coder-next.json` | qwen3-coder-next | 7/8 | 50,4 s | dauer: Syntaxfehler (Zeile 5) |
| `2026-10-22_055912_code_litellm_qwen3.6-35b.json` | qwen3.6-35b | 7/8 | 35,9 s | iban: AssertionError |
| `2026-10-22_060227_code_litellm_gemma4-31b.json` | gemma4-31b | 8/8 | 161,9 s | – |
| `2026-10-22_060314_code_claude_claude-haiku-5-5.json` | claude-haiku-5-5 | 8/8 | 11,7 s | – |
| `2026-10-22_063704_code_litellm_wamo-allgemein.json` | wamo-allgemein | 8/8 | 48,5 s | – |
| `2026-10-22_063816_code_litellm_wamo-review.json` | wamo-review | 8/8 | 37,9 s | – |

## Lange Texte und Analyse (Bericht mit 24 Abschnitten, 6 Fragen)

| Datei | Modell | richtig je Lauf | Zeit je Lauf | Eingabe-Token | Fehler |
| --- | --- | --- | --- | --- | --- |
| `2026-10-22_060344_analyse_litellm_qwen3.6-35b.json` | qwen3.6-35b | 6/6 / 6/6 | 11,1 s / 3,3 s | 5510 | – |
| `2026-10-22_060429_analyse_litellm_qwen3.8-27b.json` | qwen3.8-27b | 5/6 / 6/6 | 8,4 s / 1,7 s | 5510 | Frage 4: falsch (6) |
| `2026-10-22_060519_analyse_litellm_gemma4-31b.json` | gemma4-31b | 6/6 / 6/6 | 24,8 s / 8,4 s | 5697 | – |
| `2026-10-22_062937_analyse_claude_claude-haiku-5-5.json` | claude-haiku-5-5 | 5/6 / 6/6 | 2,0 s / 1,5 s | 12498 | Frage 4: falsche Zahl (4 Risiken (R1, R3, R5, R7, R8 – das sind 5; korrig) |
| `2026-10-22_063836_analyse_litellm_wamo-allgemein.json` | wamo-allgemein | 6/6 / 6/6 | 2,0 s / 1,7 s | 5510 | – |

## Bewerter (9 Fälle, davon einer mit Anweisung in der Kandidatenantwort)

| Datei | Modell | richtig | Zeit je Urteil (p50) |
| --- | --- | --- | --- |
| `2026-10-22_055254_bewerter_litellm_laaj-judge.json` | laaj-judge | 9/9 | 1,3 s |

## Einbettung

| Datei | Modell | Dimension | Treffer | sechs Dokumente | einzelne Frage p50 / max |
| --- | --- | --- | --- | --- | --- |
| `2026-10-22_063427_einbettung_litellm_qwen3-embedding.json` | qwen3-embedding | 4096 | 6/6 | 5,1 s | 0,2 s / 0,2 s |
| `2026-10-22_063459_einbettung_litellm_wamo-embed.json` | wamo-embed | 4096 | 6/6 | 0,5 s | 0,2 s / 0,2 s |

## Antwortzeit wie im Live-Gespräch (erstes Token, Streaming)

kurz = kleiner Prompt; voll = Persona und 17 Werkzeuge (rund 3 500 Token); neu = Anfang des Prompts ist dem Modell neu (kein Prompt-Cache); gleich = derselbe Prompt sofort noch einmal.

| Datei | Modell | kurz 1 | kurz 2 | voll neu 1 | voll gleich | voll neu 2 | kurz nach Ruhe | kurz nach anderen Modellen |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `2026-10-22_054917_latenz_litellm_gemma4-31b.json` | gemma4-31b | 8,7 s | 0,5 s | 7,4 s | 1,4 s | 7,4 s | – | – |
| `2026-10-22_055009_latenz_litellm_sokrates-fast.json` | sokrates-fast | 8,7 s | 0,6 s | 7,2 s | 1,5 s | 7,2 s | – | – |
| `2026-10-22_055050_latenz_litellm_qwen3.6-35b.json` | qwen3.6-35b | 7,9 s | 0,4 s | 2,3 s | 2,4 s | 2,3 s | – | – |
| `2026-10-22_055125_latenz_litellm_qwen3.8-27b.json` | qwen3.8-27b | 0,5 s | 0,2 s | 5,8 s | 0,2 s | 1,4 s | – | – |
| `2026-10-22_061157_latenz_litellm_gemma4-31b.json` | gemma4-31b | 0,9 s | 0,6 s | 7,5 s | 1,5 s | 7,5 s | 0,8 s nach 330 s | – |
| `2026-10-22_063059_latenz_litellm_wamo-allgemein.json` | wamo-allgemein | 0,4 s | 0,2 s | 1,6 s | 0,2 s | 1,4 s | – | – |
| `2026-10-22_063225_latenz_litellm_gemma4-31b.json` | gemma4-31b | 8,7 s | 0,6 s | 7,5 s | 1,4 s | 7,5 s | – | 0,7 s / 0,6 s nach 3 Modellen |
| `2026-10-22_063541_latenz_litellm_wamo-review.json` | wamo-review | – | – | – | – | – | – | – |

## Harmlose Frage "Wofür bist du gedacht?"

| Datei | Modell | Zeit | Antwort |
| --- | --- | --- | --- |
| `2026-10-22_055135_kennenlernen_litellm_sokrates-fable-qwen27.json` | sokrates-fable-qwen27 | 1,6 s | Ich bin ein KI-Assistent, der entwickelt wurde, um dich bei Fragen, kreativen Aufgaben und verschiedenen Alltagsproblemen zu unterstützen. |
| `2026-10-22_055155_kennenlernen_litellm_sokrates-steuer.json` | sokrates-steuer | 10,5 s | Ich bin für die Beantwortung von Fragen und das Lösen von Problemen gedacht. |
| `2026-10-22_063006_kennenlernen_litellm_wamo-allgemein.json` | wamo-allgemein | 1,2 s | Ich bin ein KI-Assistent, der entwickelt wurde, um dir bei Fragen, Aufgaben und kreativen Projekten zu helfen. |
| `2026-10-22_063027_kennenlernen_litellm_wamo-review.json` | wamo-review | 12,5 s | Ich bin ein KI-Assistent, der darauf ausgelegt ist, Fragen zu beantworten, Informationen bereitzustellen und bei verschiedenen Aufgaben zu helfen. |

---

WAMOCON GmbH · Seite 1
