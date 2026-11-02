# Schrift: Roboto Condensed (Webfont-Ersatz für Arial Narrow)

Die Oberfläche liefert die Schrift selbst aus (`next/font/local` in `layout.tsx`), damit der Bau und der Betrieb nichts bei Google holen.

| Datei | Schnitt | SHA-256 |
| --- | --- | --- |
| `roboto-condensed-latin-400-normal.woff2` | Regular, Teilmenge latin (enthält ä, ö, ü, ß) | `543c03fff71d1b39590af102e0852c2dbce46f4dbe4faaebafbef6edc82e78f5` |
| `roboto-condensed-latin-700-normal.woff2` | Bold, Teilmenge latin | `ab74f0c2d7ec37e44e017b9586675dd00c519591207dc3bbc8e003c5628dc9b7` |

Quelle: npm-Paket `@fontsource/roboto-condensed` 5.3.0 (Dateien unter `files/`). Lizenz: SIL Open Font License 1.1, Text in
`LICENSE-RobotoCondensed-OFL.txt`. Aktualisieren: neue Fassung des Pakets holen, beide Dateien ersetzen und die Prüfsummen hier und im Test
(`ui/src/lib/schrift.test.ts`) nachziehen.
