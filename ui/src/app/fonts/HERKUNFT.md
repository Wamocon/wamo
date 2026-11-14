# Schriften der Oberfläche

Die Oberfläche liefert ihre Schriften selbst aus (`next/font/local` in `layout.tsx`), damit der Bau und der Betrieb nichts bei Google holen.
Drei Familien, je eine Aufgabe: Unbounded für Marke, Überschriften und große Zahlen (`--font-display`), JetBrains Mono für Beschriftungen,
Zähler und Code (`--font-hud`), Atkinson Hyperlegible für Fließtext (`--font-body`). Alle Dateien sind die Teilmenge latin (enthält ä, ö, ü, ß).

| Datei | Schnitt | SHA-256 |
| --- | --- | --- |
| `unbounded-latin-500-normal.woff2` | Unbounded Medium | `be65bb9b1a340d1647024392fe3fbf3c21e595eda0395da3138932a01e6a183a` |
| `unbounded-latin-700-normal.woff2` | Unbounded Bold | `477fa23002517a32f232316d859e7b672a044f20073babec8ed3dbcfdb8e041a` |
| `unbounded-latin-800-normal.woff2` | Unbounded ExtraBold | `fe6d4ea7ed9647abf1e100a703786338d7898d56d0bedcdfdbc325e8fb02c834` |
| `jetbrains-mono-latin-400-normal.woff2` | JetBrains Mono Regular | `14425ba9c695763c1547f48a206b7aa60350a33ae23de09f0407877f3fcd89eb` |
| `jetbrains-mono-latin-500-normal.woff2` | JetBrains Mono Medium | `cb182feeed4d798ff6961d3c79f7026279448fca0676438aaecb21f3fc39553a` |
| `jetbrains-mono-latin-600-normal.woff2` | JetBrains Mono SemiBold | `400c6bfda18d5d14acad1c15d6dcb9f8e13c015e7286317e0b9a482539bef147` |
| `jetbrains-mono-latin-700-normal.woff2` | JetBrains Mono Bold | `d0d4e818808f2a0ba39b2b09d1989366f63494e295f003c7ef436697378507e8` |
| `atkinson-hyperlegible-latin-400-normal.woff2` | Atkinson Hyperlegible Regular | `d64ba838ef5472bba248620ec4fd8b5aa7cf0db2908e0bb230600caf279ba7bc` |
| `atkinson-hyperlegible-latin-700-normal.woff2` | Atkinson Hyperlegible Bold | `140e2bd25a7315c8a062508391426b0d8c3297400c947b8d847be28f73a199f0` |

Quellen (Dateien jeweils unter `files/` des Pakets), Lizenz jeweils SIL Open Font License 1.1:

| Familie | npm-Paket | Lizenztext |
| --- | --- | --- |
| Unbounded | `@fontsource/unbounded` 5.3.0 | `LICENSE-Unbounded-OFL.txt` |
| JetBrains Mono | `@fontsource/jetbrains-mono` 5.3.0 | `LICENSE-JetBrainsMono-OFL.txt` |
| Atkinson Hyperlegible | `@fontsource/atkinson-hyperlegible` 5.3.0 | `LICENSE-AtkinsonHyperlegible-OFL.txt` |

Aktualisieren: neue Fassung des Pakets holen, die Dateien ersetzen und die Prüfsummen hier und im Test (`ui/src/lib/schrift.test.ts`) nachziehen.
